"""Looking for ownership proofs: the DNS record, the file, and recording the result."""

import ssl
import uuid
from collections.abc import Callable, Coroutine, Mapping
from typing import Any

import dns.rdata
import dns.rdataclass
import dns.rdatatype
import httpx
import pytest
from httpx import AsyncClient

from nightloom_api.auth.models import Role, User
from nightloom_api.infra import Infra
from nightloom_api.ownership.models import OwnershipVerification
from nightloom_api.ownership.service import CheckResult, Proof
from nightloom_sdk.http import DestinationNotAllowedError
from nightloom_shuttle_dns.client import Answer, DnsQueryError
from nightloom_worker.ownership import MAX_FILE_BYTES, Prover, check_verification, guarded_client
from support import PREFIX, csrf, login

TOKEN = "s3cr3t-token"
PROOF = f"nightloom-verification={TOKEN}"
FILE_URL = "https://example.org/.well-known/nightloom-verification.txt"

UserFactory = Callable[..., Coroutine[Any, Any, User]]
ClientFactory = Callable[[], AsyncClient]


class FakeDns:
    def __init__(self, table: Mapping[tuple[str, str], tuple[str, ...]], *, fail: bool = False):
        self.table = table
        self.fail = fail
        self.asked: list[tuple[str, str]] = []

    async def query(self, name: str, rdtype: dns.rdatatype.RdataType) -> Answer | None:
        kind = dns.rdatatype.to_text(rdtype)
        self.asked.append((name, kind))
        if self.fail:
            raise DnsQueryError("Timeout")
        texts = self.table.get((name, kind))
        if texts is None:
            return None
        records = tuple(dns.rdata.from_text(dns.rdataclass.IN, rdtype, t) for t in texts)
        return Answer(name=name, records=records)


ADDRESSES: Mapping[tuple[str, str], tuple[str, ...]] = {
    ("example.org", "A"): ("93.184.215.14", "10.0.0.1"),
    ("example.org", "AAAA"): ("2001:4860::1", "::1"),
}


def prover(
    dns_table: Mapping[tuple[str, str], tuple[str, ...]] | None = None,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
    *,
    dns_fails: bool = False,
) -> Prover:
    table = {**ADDRESSES, **(dns_table or {})}
    transport = httpx.MockTransport(handler or (lambda _: httpx.Response(404)))
    return Prover(dns=FakeDns(table, fail=dns_fails), http=lambda: guarded_client(transport))


# --- the DNS record ----------------------------------------------------------------------------


async def test_txt_record_found_with_the_public_addresses() -> None:
    record = {("_nightloom-challenge.example.org", "TXT"): ('"other"', f'"{PROOF}"')}
    proof = await prover(record).check("example.org", "dns_txt", TOKEN)
    # Private and loopback addresses are never covered.
    assert proof == Proof(CheckResult.FOUND, addresses=("93.184.215.14", "2001:4860::1"))


async def test_txt_record_split_into_strings_still_counts() -> None:
    half = len(PROOF) // 2
    record = {("_nightloom-challenge.example.org", "TXT"): (f'"{PROOF[:half]}" "{PROOF[half:]}"',)}
    assert (await prover(record).check("example.org", "dns_txt", TOKEN)).result == "found"


async def test_txt_record_missing_or_wrong() -> None:
    missing = await prover().check("example.org", "dns_txt", TOKEN)
    assert missing == Proof(CheckResult.MISSING, "record_not_found")
    wrong = {("_nightloom-challenge.example.org", "TXT"): ('"nightloom-verification=nope"',)}
    assert (await prover(wrong).check("example.org", "dns_txt", TOKEN)).error == "record_not_found"
    down = await prover(dns_fails=True).check("example.org", "dns_txt", TOKEN)
    assert down == Proof(CheckResult.UNREACHABLE, "dns_unreachable")


# --- the file ----------------------------------------------------------------------------------


async def test_file_found() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=f"# Nightloom\n{PROOF}\n")

    proof = await prover(handler=handler).check("example.org", "http_file", TOKEN)
    assert proof.result == CheckResult.FOUND
    assert [str(r.url) for r in seen] == [FILE_URL]
    assert seen[0].headers["User-Agent"].startswith("Nightloom/")


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(404), Proof(CheckResult.MISSING, "file_not_found")),
        (httpx.Response(403), Proof(CheckResult.MISSING, "file_not_found")),
        (httpx.Response(503), Proof(CheckResult.UNREACHABLE, "site_unreachable")),
        (
            httpx.Response(301, headers={"Location": "https://elsewhere.test/proof"}),
            Proof(CheckResult.MISSING, "redirected"),
        ),
        (
            httpx.Response(200, text="nightloom-verification=nope"),
            Proof(CheckResult.MISSING, "token_mismatch"),
        ),
        (
            httpx.Response(200, text=f"x{PROOF}"),
            Proof(CheckResult.MISSING, "token_mismatch"),
        ),
        (
            httpx.Response(200, text=PROOF + "\n" + "x" * MAX_FILE_BYTES),
            Proof(CheckResult.MISSING, "token_mismatch"),
        ),
    ],
)
async def test_file_not_proving(response: httpx.Response, expected: Proof) -> None:
    proof = await prover(handler=lambda _: response).check("example.org", "http_file", TOKEN)
    assert proof == expected


async def test_file_network_failures() -> None:
    def private(request: httpx.Request) -> httpx.Response:
        raise DestinationNotAllowedError("private", request=request)

    def tls(request: httpx.Request) -> httpx.Response:
        try:
            raise ssl.SSLCertVerificationError("certificate verify failed")
        except ssl.SSLError as error:
            raise httpx.ConnectError("TLS", request=request) from error

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    for handler, expected in [
        (private, Proof(CheckResult.MISSING, "not_public")),
        (tls, Proof(CheckResult.UNREACHABLE, "tls_error")),
        (down, Proof(CheckResult.UNREACHABLE, "site_unreachable")),
    ]:
        assert await prover(handler=handler).check("example.org", "http_file", TOKEN) == expected


async def test_addresses_need_an_answer() -> None:
    assert await prover().addresses("example.org") == ("93.184.215.14", "2001:4860::1")
    assert await prover().addresses("nothing.example.org") == ()
    assert await prover(dns_fails=True).addresses("example.org") is None


# --- recording a check -------------------------------------------------------------------------


@pytest.mark.db
async def test_check_verification_records_the_result(
    infra: Infra, make_user: UserFactory, new_client: ClientFactory
) -> None:
    owner = await make_user("owner@example.org", Role.ANALYST)
    client = new_client()
    assert (await login(client, email=owner.email)).status_code == 200
    created = await client.post(
        f"{PREFIX}/cases",
        json={
            "title": "Own site",
            "purpose": "Check the public exposure of our own site example.org.",
            "legal_basis": "owned_assets",
            "ethics_acknowledged": True,
        },
        headers=csrf(client),
    )
    case_id = created.json()["id"]
    added = await client.post(
        f"{PREFIX}/cases/{case_id}/verifications",
        json={"domain": "example.org", "method": "http_file"},
        headers=csrf(client),
    )
    item = added.json()
    value = item["proof"]["value"]
    token = value.removeprefix("nightloom-verification=")
    found = Prover(
        dns=FakeDns(ADDRESSES),
        http=lambda: guarded_client(httpx.MockTransport(lambda _: httpx.Response(200, text=value))),
    )
    verification_id = uuid.UUID(item["id"])
    async with infra.sessionmaker() as db:
        row = await db.get(OwnershipVerification, verification_id)
        assert row is not None and row.token == token
        row.check_renews = True
        await db.commit()
    assert await check_verification(infra.sessionmaker, verification_id, found) == "verified"
    listed = (await client.get(f"{PREFIX}/cases/{case_id}/verifications")).json()["items"]
    assert (listed[0]["status"], listed[0]["addresses"]) == (
        "verified",
        ["93.184.215.14", "2001:4860::1"],
    )
    # Removed meanwhile: nothing is recorded.
    assert await check_verification(infra.sessionmaker, uuid.uuid4(), found) is None
