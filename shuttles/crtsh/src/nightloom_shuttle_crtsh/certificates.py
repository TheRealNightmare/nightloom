"""crt.sh search rows and downloaded certificates.

A crt.sh JSON search result is a list of rows, one per logged certificate
(precertificates and final certificates are separate rows with separate
IDs). :func:`parse_entries` validates each row with :class:`CrtShEntry` and
counts the rows it ignores instead of failing on them.

crt.sh's JSON has no fingerprint, so the Shuttle downloads a certificate
(``?d=<id>``, PEM) and :func:`parse_certificate` reads it with
``cryptography``: the SHA-256 fingerprint of the DER encoding is the
Certificate's key.
"""

import hashlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# Generous bounds on a row's fields; anything larger is a malformed row.
MAX_NAME_VALUE_LENGTH = 200_000
MAX_COMMON_NAME_LENGTH = 1_000
ISSUER_MAX_LENGTH = 1_024
SUBJECT_CN_MAX_LENGTH = 200

_PEM_MARKER = b"-----BEGIN CERTIFICATE-----"


def _utc(value: datetime) -> datetime:
    """crt.sh writes UTC times without an offset."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class CrtShEntry(BaseModel):
    """One row of crt.sh's JSON output; unknown fields are ignored."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: Annotated[int, Field(ge=1)]
    name_value: Annotated[str, Field(max_length=MAX_NAME_VALUE_LENGTH)]
    common_name: Annotated[str, Field(max_length=MAX_COMMON_NAME_LENGTH)] | None = None
    not_before: datetime
    not_after: datetime | None = None

    @field_validator("not_before", "not_after")
    @classmethod
    def _as_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _utc(value)

    def identities(self) -> Iterator[str]:
        """Every name the row lists: the SANs (one per line), then the common name."""
        yield from self.name_value.splitlines()
        if self.common_name:
            yield self.common_name


def parse_entries(rows: Iterable[Any]) -> tuple[list[CrtShEntry], int]:
    """The valid rows, unique by ID, most recent first; and how many rows were malformed."""
    entries: dict[int, CrtShEntry] = {}
    malformed = 0
    for row in rows:
        try:
            entry = CrtShEntry.model_validate(row)
        except ValidationError:
            malformed += 1
            continue
        entries.setdefault(entry.id, entry)
    ordered = sorted(entries.values(), key=lambda e: (e.not_before, e.id), reverse=True)
    return ordered, malformed


@dataclass(frozen=True)
class ParsedCertificate:
    """What the Shuttle records about a downloaded certificate."""

    fingerprint: str  # SHA-256 of the DER encoding, lower-case hex
    subject_cn: str | None
    issuer: str
    serial_number: str  # lower-case hex
    not_before: datetime
    not_after: datetime
    self_signed: bool
    san_count: int
    names: tuple[str, ...]  # the DNS names in the SANs, then the subject's common name


def _without_email(name: x509.Name) -> x509.Name:
    """``name`` minus any emailAddress attributes (person data)."""
    kept = [
        x509.RelativeDistinguishedName(
            [a for a in rdn if a.oid != NameOID.EMAIL_ADDRESS and "@" not in str(a.value)]
        )
        for rdn in name.rdns
        if any(a.oid != NameOID.EMAIL_ADDRESS and "@" not in str(a.value) for a in rdn)
    ]
    return x509.Name(kept)


def _common_name(certificate: x509.Certificate) -> str | None:
    attributes = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if not attributes:
        return None
    value = str(attributes[0].value).strip()
    if not value or "@" in value:
        return None
    return value[:SUBJECT_CN_MAX_LENGTH]


def _self_signed(certificate: x509.Certificate) -> bool:
    if certificate.issuer != certificate.subject:
        return False
    try:
        certificate.verify_directly_issued_by(certificate)
    except (ValueError, TypeError, InvalidSignature, UnsupportedAlgorithm):
        return False
    return True


def _alternative_names(certificate: x509.Certificate) -> tuple[int, list[str]]:
    try:
        extension = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return 0, []
    return len(extension.value), list(extension.value.get_values_for_type(x509.DNSName))


def load_certificate(data: bytes) -> x509.Certificate:
    """A PEM or DER certificate; raises ``ValueError`` if it is neither."""
    if _PEM_MARKER in data:
        return x509.load_pem_x509_certificate(data)
    return x509.load_der_x509_certificate(data)


def parse_certificate(data: bytes) -> ParsedCertificate:
    """Read a downloaded certificate; raises ``ValueError`` if it cannot be read."""
    certificate = load_certificate(data)
    der = certificate.public_bytes(Encoding.DER)
    san_count, dns_names = _alternative_names(certificate)
    common_name = _common_name(certificate)
    names = tuple(dict.fromkeys([*dns_names, *([common_name] if common_name else [])]))
    serial = certificate.serial_number
    return ParsedCertificate(
        fingerprint=hashlib.sha256(der).hexdigest(),
        subject_cn=common_name,
        issuer=_without_email(certificate.issuer).rfc4514_string()[:ISSUER_MAX_LENGTH],
        serial_number=f"{serial:x}" if serial >= 0 else f"-{-serial:x}",
        not_before=certificate.not_valid_before_utc,
        not_after=certificate.not_valid_after_utc,
        self_signed=_self_signed(certificate),
        san_count=san_count,
        names=names,
    )


__all__ = [
    "CrtShEntry",
    "ParsedCertificate",
    "load_certificate",
    "parse_certificate",
    "parse_entries",
]
