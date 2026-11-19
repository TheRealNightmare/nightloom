"""The shared redaction check (used by the RDAP and RIPEstat Shuttles)."""

import pytest

from nightloom_sdk.redaction import REDACTION_MARKERS, is_redacted


@pytest.mark.parametrize("marker", REDACTION_MARKERS)
def test_every_marker_matches_in_any_case(marker: str) -> None:
    assert is_redacted(f"Name {marker.upper()} here")
    assert is_redacted(marker.title())


@pytest.mark.parametrize(
    "value",
    ["Example Hosting LLC", "Example Private Limited", "EXAMPLE-NET - Example Networks B.V.", ""],
)
def test_ordinary_names_are_not_redacted(value: str) -> None:
    assert not is_redacted(value)
