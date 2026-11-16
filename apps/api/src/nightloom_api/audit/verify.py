"""Checking the audit log's hash chain.

Rows are read in ID order with keyset pagination (:data:`BATCH_SIZE` at a
time). Each batch is hashed in a worker thread, so a long chain never blocks
the event loop for more than one batch's worth of database I/O.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

import anyio.to_thread
from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncSession

from nightloom_api.audit.chain import GENESIS_HASH, chain_payload, compute_hash
from nightloom_api.audit.models import AuditEvent

BATCH_SIZE = 1000

REASON_PREV_HASH = "prev_hash does not match the previous event's hash"
REASON_HASH = "hash does not match the event's contents"

_COLUMNS = (
    AuditEvent.id,
    AuditEvent.occurred_at,
    AuditEvent.actor_user_id,
    AuditEvent.actor_label,
    AuditEvent.actor_ip,
    AuditEvent.request_id,
    AuditEvent.action,
    AuditEvent.outcome,
    AuditEvent.target_type,
    AuditEvent.target_id,
    AuditEvent.case_id,
    AuditEvent.details,
    AuditEvent.prev_hash,
    AuditEvent.hash,
)


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    checked: int
    first_bad_id: int | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _BatchResult:
    checked: int
    last_hash: str
    first_bad_id: int | None = None
    reason: str | None = None


def check_batch(rows: Sequence[Row[Any]], expected_prev: str) -> _BatchResult:
    """Recompute and compare the hashes of consecutive rows (CPU only; run in a thread)."""
    checked = 0
    for row in rows:
        checked += 1
        if row.prev_hash != expected_prev:
            return _BatchResult(checked, expected_prev, row.id, REASON_PREV_HASH)
        payload = chain_payload(
            **{column.key: getattr(row, column.key) for column in _COLUMNS[:-1]}
        )
        if compute_hash(payload) != row.hash:
            return _BatchResult(checked, expected_prev, row.id, REASON_HASH)
        expected_prev = row.hash
    return _BatchResult(checked, expected_prev)


async def verify_chain(db: AsyncSession, *, batch_size: int = BATCH_SIZE) -> VerifyResult:
    """Walk the whole chain; report the first event that does not check out.

    A tail that was cut off cannot be detected from inside the database (see
    "Audit log" in docs/ARCHITECTURE.md).
    """
    checked = 0
    expected_prev = GENESIS_HASH
    last_id = 0
    while True:
        rows = (
            await db.execute(
                select(*_COLUMNS)
                .where(AuditEvent.id > last_id)
                .order_by(AuditEvent.id)
                .limit(batch_size)
            )
        ).all()
        if not rows:
            return VerifyResult(ok=True, checked=checked)
        result = await anyio.to_thread.run_sync(check_batch, rows, expected_prev)
        checked += result.checked
        if result.first_bad_id is not None:
            return VerifyResult(
                ok=False, checked=checked, first_bad_id=result.first_bad_id, reason=result.reason
            )
        expected_prev = result.last_hash
        last_id = rows[-1].id
