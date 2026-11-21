"""Request and response bodies for ownership verifications."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nightloom_api.ownership import service
from nightloom_api.ownership.models import Method, OwnershipVerification


class VerificationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # A domain the case's owners control; it covers its subdomains too.
    domain: str = Field(min_length=1, max_length=300)
    method: Method


class ProofOut(BaseModel):
    """Where to publish the proof, and what it must say."""

    # dns_txt: a TXT record named ``record_name``; http_file: the body of ``url``.
    record_type: Literal["TXT"] | None
    record_name: str | None
    url: str | None
    value: str


class VerificationOut(BaseModel):
    id: uuid.UUID
    domain: str
    method: Method
    status: str
    proof: ProofOut
    # A check is queued and has not reported yet.
    checking: bool
    created_at: datetime
    checked_at: datetime | None
    verified_at: datetime | None
    expires_at: datetime | None
    # Why the last check did not find the proof: a code and its explanation.
    last_error: str | None
    error_message: str | None
    # What the domain resolved to at the last successful check (covered too).
    addresses: list[str]

    @classmethod
    def build(cls, row: OwnershipVerification) -> "VerificationOut":
        value = service.proof_value(row.token)
        dns = row.method == Method.DNS_TXT
        return cls(
            id=row.id,
            domain=row.domain,
            method=Method(row.method),
            status=row.status,
            proof=ProofOut(
                record_type="TXT" if dns else None,
                record_name=service.record_name(row.domain) if dns else None,
                url=None if dns else service.file_url(row.domain),
                value=value,
            ),
            checking=row.check_requested_at is not None,
            created_at=row.created_at,
            checked_at=row.checked_at,
            verified_at=row.verified_at,
            expires_at=row.expires_at,
            last_error=row.last_error,
            error_message=service.ERRORS.get(row.last_error or ""),
            addresses=list(row.addresses),
        )


class VerificationList(BaseModel):
    items: list[VerificationOut]
