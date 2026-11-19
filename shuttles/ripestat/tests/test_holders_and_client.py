"""Holder parsing, the response models and the client's helpers."""

from datetime import UTC, datetime

import httpx
import pytest

from nightloom_shuttle_ripestat.client import (
    BASE_URL,
    InvalidResponseError,
    RipeStatClient,
    data_url,
    retry_after_seconds,
)
from nightloom_shuttle_ripestat.holders import NAME_MAX_LENGTH, Holder, parse_holder
from nightloom_shuttle_ripestat.models import Timeline


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # The usual shape: network name, " - ", organization.
        (
            "EXAMPLE-NET - Example Networks B.V.",
            Holder(as_name="EXAMPLE-NET", organization="Example Networks B.V."),
        ),
        # Split on the FIRST " - " only.
        (
            "EXAMPLE-AS - Example - Networks, Inc.",
            Holder(as_name="EXAMPLE-AS", organization="Example - Networks, Inc."),
        ),
        # A dash without spaces is part of the name, so there is no separator.
        ("EXAMPLE-AS", Holder(organization="EXAMPLE-AS")),
        ("Example Networks B.V.", Holder(organization="Example Networks B.V.")),
        # Extra spaces, tabs and newlines are trimmed and collapsed.
        (
            "  EXAMPLE-NET  -   Example   Networks\tB.V.  ",
            Holder(as_name="EXAMPLE-NET", organization="Example Networks B.V."),
        ),
        ("EXAMPLE-NET\n-\nExample Networks", Holder("EXAMPLE-NET", "Example Networks")),
        # A missing half.
        ("EXAMPLE-NET - ", Holder(as_name="EXAMPLE-NET")),
        ("EXAMPLE-NET -", Holder(as_name="EXAMPLE-NET")),
        (" - Example Networks", Holder(organization="Example Networks")),
        ("- Example Networks", Holder(organization="Example Networks")),
        # Empty, blank, punctuation only, not text.
        ("", Holder()),
        ("   ", Holder()),
        (" - ", Holder()),
        ("-", Holder()),
        ("EXAMPLE-NET - ---", Holder(as_name="EXAMPLE-NET")),
        (None, Holder()),
        (64496, Holder()),
        # Redacted-looking parts are dropped and flagged.
        (
            "EXAMPLE-NET - REDACTED FOR PRIVACY",
            Holder(as_name="EXAMPLE-NET", redacted=True),
        ),
        ("Data Protected", Holder(redacted=True)),
        ("GDPR Masked - Example Networks", Holder(organization="Example Networks", redacted=True)),
        # "Private" is part of real company names and is not a redaction marker.
        (
            "EXAMPLE-IN - Example Private Limited",
            Holder(as_name="EXAMPLE-IN", organization="Example Private Limited"),
        ),
        # Too long to be a name.
        ("EXAMPLE-NET - " + "x" * (NAME_MAX_LENGTH + 1), Holder(as_name="EXAMPLE-NET")),
    ],
)
def test_parse_holder(value: object, expected: Holder) -> None:
    assert parse_holder(value) == expected


def test_timelines_are_utc() -> None:
    naive = Timeline.model_validate({"starttime": "2026-11-04T16:00:00", "endtime": None})
    assert naive.starttime == datetime(2026, 11, 4, 16, tzinfo=UTC)
    assert naive.endtime is None
    aware = Timeline.model_validate({"starttime": "2026-11-04T18:00:00+02:00"})
    assert aware.starttime == datetime(2026, 11, 4, 16, tzinfo=UTC)


def test_data_url() -> None:
    assert BASE_URL == "https://stat.ripe.net/data/"
    assert data_url("network-info") == "https://stat.ripe.net/data/network-info/data.json"


def test_retry_after() -> None:
    now = datetime(2026, 11, 19, 12, 0, 0, tzinfo=UTC)
    assert retry_after_seconds(None) is None
    assert retry_after_seconds(" 12 ") == 12.0
    assert retry_after_seconds("Thu, 19 Nov 2026 12:00:10 GMT", now=now) == 10.0
    assert retry_after_seconds("Thu, 19 Nov 2026 11:59:00 GMT", now=now) == 0.0
    assert retry_after_seconds("Thu, 19 Nov 2026 12:00:10", now=now) == 10.0  # no zone: UTC
    assert retry_after_seconds("soon") is None


async def test_client_sends_resource_and_sourceapp() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "ok", "data": {"asns": [], "prefix": ""}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = RipeStatClient(http)
        data = await client.get("network-info", "2001:db8::1")
    assert data == {"asns": [], "prefix": ""}
    (request,) = seen
    assert request.url.path == "/data/network-info/data.json"
    assert dict(request.url.params) == {"resource": "2001:db8::1", "sourceapp": "nightloom"}
    assert client.requests == 1


async def test_client_reports_non_ok_status_without_quoting_it() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"status": "error", "messages": [["error", "secret-ish detail"]], "data": {}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(InvalidResponseError) as error:
            await RipeStatClient(http).get("as-overview", "AS64496")
    assert "secret-ish" not in str(error.value)
