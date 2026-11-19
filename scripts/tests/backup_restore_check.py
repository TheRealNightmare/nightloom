"""Seeds data before a backup and checks it after a restore (scripts/test-backup-restore.sh).

    python scripts/tests/backup_restore_check.py seed   --state FILE
    python scripts/tests/backup_restore_check.py verify --state FILE

Talks to the API over HTTP(S) only, as an admin (NIGHTLOOM_TEST_ADMIN_EMAIL /
NIGHTLOOM_TEST_ADMIN_PASSWORD), at NIGHTLOOM_TEST_BASE_URL. For the HTTPS
edge, NIGHTLOOM_TEST_HOST is the name presented (SNI and Host) and
NIGHTLOOM_TEST_CA the CA certificate to verify it with.

`seed` creates a case with two entities and a relationship (which also
records audit events) and writes what it created to the state file; the
bucket object is handled by the shell script. `verify` checks the case and
its graph are back and that the audit chain still verifies.
"""

import argparse
import json
import os
import ssl
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

PREFIX = "/api/v1"


def client() -> httpx.Client:
    base_url = os.environ["NIGHTLOOM_TEST_BASE_URL"]
    host = os.environ.get("NIGHTLOOM_TEST_HOST", "")
    ca = os.environ.get("NIGHTLOOM_TEST_CA", "")
    verify: ssl.SSLContext | bool = ssl.create_default_context(cafile=ca) if ca else True
    headers = {"Host": host} if host else {}
    return httpx.Client(base_url=base_url, headers=headers, verify=verify, timeout=30)


def request(http: httpx.Client, method: str, path: str, **kwargs: Any) -> httpx.Response:
    host = os.environ.get("NIGHTLOOM_TEST_HOST", "")
    if host and str(http.base_url).startswith("https://"):
        kwargs.setdefault("extensions", {})["sni_hostname"] = host
    csrf = http.cookies.get("nightloom_csrf")
    if csrf and method != "GET":
        kwargs.setdefault("headers", {})["X-CSRF-Token"] = csrf
    return http.request(method, f"{PREFIX}{path}", **kwargs)


def expect(response: httpx.Response, *statuses: int) -> Any:
    if response.status_code not in statuses:
        sys.exit(
            f"{response.request.method} {response.request.url.path}: "
            f"{response.status_code} {response.text}"
        )
    return response.json() if response.content else None


def sign_in(http: httpx.Client) -> None:
    expect(
        request(
            http,
            "POST",
            "/auth/login",
            json={
                "email": os.environ["NIGHTLOOM_TEST_ADMIN_EMAIL"],
                "password": os.environ["NIGHTLOOM_TEST_ADMIN_PASSWORD"],
            },
        ),
        200,
    )


def seed(state_file: Path) -> None:
    marker = uuid.uuid4().hex[:10]
    with client() as http:
        sign_in(http)
        case = expect(
            request(
                http,
                "POST",
                "/cases",
                json={
                    "title": f"Backup round trip {marker}",
                    "purpose": "Check that a backup and restore bring this case back intact.",
                    "legal_basis": "self_assessment",
                    "ethics_acknowledged": True,
                },
            ),
            201,
        )
        case_path = f"/cases/{case['id']}"
        domain = f"backup-{marker}.example.org"
        keys = []
        for entity in (
            {"type": "domain", "value": domain},
            {"type": "ip_address", "value": "192.0.2.44"},
        ):
            keys.append(
                expect(request(http, "POST", f"{case_path}/entities", json=entity), 201)["key"]
            )
        expect(
            request(
                http,
                "POST",
                f"{case_path}/relationships",
                json={"type": "RESOLVES_TO", "source_key": keys[0], "target_key": keys[1]},
            ),
            201,
        )
        audit = expect(request(http, "POST", "/audit/verify"), 200)
        if not audit["ok"]:
            sys.exit(f"the audit chain does not verify before the backup: {audit}")

    state = {
        "case_id": case["id"],
        "title": case["title"],
        "keys": keys,
        "audit_checked": audit["checked"],
    }
    state_file.write_text(json.dumps(state))
    print(
        f"Seeded case {case['id']} with {len(keys)} entities; "
        f"audit chain ok ({audit['checked']} events)."
    )


def verify(state_file: Path) -> None:
    state = json.loads(state_file.read_text())
    with client() as http:
        sign_in(http)
        case = expect(request(http, "GET", f"/cases/{state['case_id']}"), 200)
        if case["title"] != state["title"]:
            sys.exit(f"case title changed: {case['title']!r}")
        graph = expect(request(http, "GET", f"/cases/{state['case_id']}/graph"), 200)
        nodes = {node["key"] for node in graph["nodes"]}
        missing = set(state["keys"]) - nodes
        if missing:
            sys.exit(f"entities missing after the restore: {sorted(missing)}")
        edges = {(e["source_key"], e["type"], e["target_key"]) for e in graph["edges"]}
        if (state["keys"][0], "RESOLVES_TO", state["keys"][1]) not in edges:
            sys.exit("the relationship is missing after the restore")
        audit = expect(request(http, "POST", "/audit/verify"), 200)
        if not audit["ok"]:
            sys.exit(f"the audit chain does not verify after the restore: {audit}")
        # The events from before the backup, plus at least the verification
        # recorded during it.
        if audit["checked"] <= state["audit_checked"]:
            sys.exit(f"audit events missing: {audit['checked']} <= {state['audit_checked']}")
    print(
        f"Restored case {state['case_id']} with its {len(state['keys'])} entities "
        "and relationship; "
        f"audit chain ok ({audit['checked']} events)."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("phase", choices=["seed", "verify"])
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    if args.phase == "seed":
        seed(args.state)
    else:
        verify(args.state)


if __name__ == "__main__":
    main()
