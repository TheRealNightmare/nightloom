"""Reading the certificate a site presented (DER, from the TLS connection)."""

import hashlib
from dataclasses import dataclass
from datetime import datetime

from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID

SUBJECT_CN_MAX_LENGTH = 200
ISSUER_MAX_LENGTH = 1024


@dataclass(frozen=True)
class PresentedCertificate:
    """What the Shuttle records about the certificate a server presented."""

    fingerprint: str  # SHA-256 of the DER encoding, lower-case hex
    subject_cn: str | None
    issuer: str | None
    serial_number: str  # lower-case hex
    not_before: datetime
    not_after: datetime
    self_signed: bool
    san_count: int


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


def _san_count(certificate: x509.Certificate) -> int:
    try:
        extension = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return 0
    return len(extension.value)


def parse_der(der: bytes) -> PresentedCertificate:
    """Read a DER certificate; raises ``ValueError`` if it cannot be read."""
    certificate = x509.load_der_x509_certificate(der)
    der = certificate.public_bytes(Encoding.DER)
    serial = certificate.serial_number
    issuer = _without_email(certificate.issuer).rfc4514_string()[:ISSUER_MAX_LENGTH]
    return PresentedCertificate(
        fingerprint=hashlib.sha256(der).hexdigest(),
        subject_cn=_common_name(certificate),
        issuer=issuer or None,
        serial_number=f"{serial:x}" if serial >= 0 else f"-{-serial:x}",
        not_before=certificate.not_valid_before_utc,
        not_after=certificate.not_valid_after_utc,
        self_signed=_self_signed(certificate),
        san_count=_san_count(certificate),
    )


__all__ = ["PresentedCertificate", "parse_der"]
