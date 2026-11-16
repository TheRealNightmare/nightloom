import pytest

from nightloom_api.infra.errors import MAX_LENGTH, describe_error


def test_uses_type_and_first_line() -> None:
    exc = ConnectionError("connection refused\ntraceback details")

    assert describe_error(exc) == "ConnectionError: connection refused"


def test_type_only_when_message_is_empty() -> None:
    assert describe_error(KeyError()) == "KeyError"


def test_masks_known_secrets_longest_first() -> None:
    url = "postgresql+asyncpg://user:hunter2@db:5432/app"
    exc = RuntimeError(f"failed {url} with hunter2")

    assert describe_error(exc, [url, "hunter2", ""]) == "RuntimeError: failed *** with ***"


@pytest.mark.parametrize(
    "message",
    [
        "cannot reach redis://:topsecret@valkey:6379/0 now",
        "endpoint http://seaweedfs:8333/bucket?X-Amz-Signature=abc failed",
        "bolt://neo4j:7687",
    ],
)
def test_strips_urls_even_without_known_secrets(message: str) -> None:
    text = describe_error(OSError(message))

    assert "://" not in text
    assert "topsecret" not in text
    assert "<url>" in text


def test_url_replacement_keeps_surrounding_quotes() -> None:
    exc = OSError('Could not connect to the endpoint URL: "http://s3:8333/bucket"')

    assert describe_error(exc) == 'OSError: Could not connect to the endpoint URL: "<url>"'


def test_strips_userinfo_without_scheme() -> None:
    text = describe_error(OSError("login admin:p4ss@host failed"))

    assert text == "OSError: login ***@host failed"


def test_truncates_long_messages() -> None:
    text = describe_error(ValueError("x" * 500))

    assert len(text) == MAX_LENGTH
    assert text.endswith("…")
