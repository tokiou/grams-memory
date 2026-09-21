"""SQLite-backed pending intervention queue."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio
import uuid

import aiosqlite


class InterventionStatus:
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    CONSUMED = "CONSUMED"
    FALLBACK_SENDING = "FALLBACK_SENDING"
    FALLBACK_DELIVERED = "FALLBACK_DELIVERED"
    DELIVERY_UNKNOWN = "DELIVERY_UNKNOWN"


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


class PendingInterventionRepository:
    """Persist and atomically deliver session-scoped interventions."""

    def __init__(self, connection: aiosqlite.Connection, *, claim_lease_seconds: float = 60.0) -> None:
        if claim_lease_seconds <= 0:
            raise ValueError("claim lease must be positive")
        self.connection = connection
        self.claim_lease_seconds = claim_lease_seconds
        self._write_lock = asyncio.Lock()

    async def initialize(self) -> None:
        await self.connection.execute(
            """CREATE TABLE IF NOT EXISTS supervisor_interventions (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                message TEXT NOT NULL,
                delivery_key TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN (
                    'PENDING', 'CLAIMED', 'CONSUMED', 'FALLBACK_SENDING',
                    'FALLBACK_DELIVERED', 'DELIVERY_UNKNOWN'
                )),
                created_at TEXT NOT NULL,
                claimed_at TEXT,
                claim_until TEXT,
                claim_token TEXT,
                consumed_at TEXT,
                consumed_claim_token TEXT,
                fallback_token TEXT,
                fallback_until TEXT,
                UNIQUE (session_id, delivery_key)
            )""",
        )
        columns = {
            row[1]
            for row in await (await self.connection.execute(
                "PRAGMA table_info(supervisor_interventions)"
            )).fetchall()
        }
        for name in ("consumed_claim_token", "fallback_token", "fallback_until"):
            if name not in columns:
                await self.connection.execute(
                    f"ALTER TABLE supervisor_interventions ADD COLUMN {name} TEXT"
                )
        await self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_interventions_pending "
            "ON supervisor_interventions(session_id, status, created_at)"
        )
        await self.connection.commit()

    @staticmethod
    def _row(row: aiosqlite.Row | None) -> dict[str, object] | None:
        return dict(row) if row is not None else None

    async def enqueue(
        self,
        session_id: str,
        message: str,
        delivery_key: str,
        *,
        initial_status: str = InterventionStatus.PENDING,
    ) -> dict[str, object]:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id is required")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message is required")
        if not isinstance(delivery_key, str) or not delivery_key.strip():
            raise ValueError("delivery_key is required")
        if initial_status not in {InterventionStatus.PENDING, InterventionStatus.FALLBACK_SENDING}:
            raise ValueError("invalid initial intervention status")

        async with self._write_lock:
            fallback_token = (
                str(uuid.uuid4())
                if initial_status == InterventionStatus.FALLBACK_SENDING
                else None
            )
            fallback_until = (
                _stamp(datetime.now(timezone.utc) + timedelta(seconds=self.claim_lease_seconds))
                if fallback_token
                else None
            )
            cursor = await self.connection.execute(
                """INSERT OR IGNORE INTO supervisor_interventions
                (id, session_id, message, delivery_key, status, created_at,
                 fallback_token, fallback_until)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    session_id,
                    message.strip(),
                    delivery_key,
                    initial_status,
                    _stamp(datetime.now(timezone.utc)),
                    fallback_token,
                    fallback_until,
                ),
            )
            await self.connection.commit()
            row = await (await self.connection.execute(
                "SELECT * FROM supervisor_interventions WHERE session_id=? AND delivery_key=?",
                (session_id, delivery_key),
            )).fetchone()
            result = dict(row) if row is not None else {}
            result["created"] = cursor.rowcount == 1
            return result

    async def get(self, intervention_id: str, session_id: str) -> dict[str, object] | None:
        row = await (await self.connection.execute(
            "SELECT * FROM supervisor_interventions WHERE id=? AND session_id=?",
            (intervention_id, session_id),
        )).fetchone()
        return self._row(row)

    async def claim(self, session_id: str) -> dict[str, object] | None:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id is required")

        async with self._write_lock:
            now = datetime.now(timezone.utc)
            now_stamp = _stamp(now)
            claim_until = _stamp(now + timedelta(seconds=self.claim_lease_seconds))
            await self.connection.execute("BEGIN IMMEDIATE")
            try:
                await self.connection.execute(
                    """UPDATE supervisor_interventions
                       SET status='DELIVERY_UNKNOWN', claim_until=NULL, claim_token=NULL
                       WHERE session_id=? AND status='CLAIMED' AND claim_until <= ?""",
                    (session_id, now_stamp),
                )
                row = await (await self.connection.execute(
                    """SELECT * FROM supervisor_interventions
                       WHERE session_id=? AND status='PENDING'
                       ORDER BY created_at, rowid LIMIT 1""",
                    (session_id,),
                )).fetchone()
                if row is None:
                    await self.connection.commit()
                    return None
                token = str(uuid.uuid4())
                await self.connection.execute(
                    """UPDATE supervisor_interventions
                       SET status='CLAIMED', claimed_at=?, claim_until=?, claim_token=?
                       WHERE id=? AND status='PENDING'""",
                    (now_stamp, claim_until, token, row["id"]),
                )
                claimed = await (await self.connection.execute(
                    "SELECT * FROM supervisor_interventions WHERE id=?", (row["id"],)
                )).fetchone()
                await self.connection.commit()
                return self._row(claimed)
            except BaseException:
                await self.connection.rollback()
                raise

    async def consume(self, intervention_id: str, session_id: str, claim_token: str) -> bool:
        if not all(isinstance(value, str) and value.strip() for value in (
            intervention_id, session_id, claim_token,
        )):
            raise ValueError("intervention_id, session_id, and claim_token are required")
        async with self._write_lock:
            cursor = await self.connection.execute(
                """UPDATE supervisor_interventions
                   SET status='CONSUMED', consumed_at=?, claim_until=NULL, claim_token=NULL,
                       consumed_claim_token=claim_token
                   WHERE id=? AND session_id=? AND status='CLAIMED' AND claim_token=?""",
                (_stamp(datetime.now(timezone.utc)), intervention_id, session_id, claim_token),
            )
            await self.connection.commit()
            if cursor.rowcount == 1:
                return True
            row = await (await self.connection.execute(
                "SELECT status, consumed_claim_token FROM supervisor_interventions WHERE id=? AND session_id=?",
                (intervention_id, session_id),
            )).fetchone()
            # A retry after a successful acknowledgement is idempotent.
            return row is not None and row["status"] == InterventionStatus.CONSUMED and row["consumed_claim_token"] == claim_token

    async def begin_fallback(self, intervention_id: str, session_id: str) -> dict[str, object] | None:
        async with self._write_lock:
            fallback_token = str(uuid.uuid4())
            cursor = await self.connection.execute(
                """UPDATE supervisor_interventions
                   SET status='FALLBACK_SENDING', claim_until=NULL, claim_token=NULL,
                       fallback_token=?, fallback_until=?
                   WHERE id=? AND session_id=? AND status='PENDING'""",
                (
                    fallback_token,
                    _stamp(datetime.now(timezone.utc) + timedelta(seconds=self.claim_lease_seconds)),
                    intervention_id,
                    session_id,
                ),
            )
            await self.connection.commit()
            if cursor.rowcount != 1:
                return None
            return await self.get(intervention_id, session_id)

    async def mark_fallback_delivered(
        self,
        intervention_id: str,
        session_id: str,
        fallback_token: str,
    ) -> bool:
        async with self._write_lock:
            cursor = await self.connection.execute(
                """UPDATE supervisor_interventions
                   SET status='FALLBACK_DELIVERED', consumed_at=?, claim_until=NULL,
                       claim_token=NULL, fallback_until=NULL
                   WHERE id=? AND session_id=? AND status='FALLBACK_SENDING' AND fallback_token=?""",
                (_stamp(datetime.now(timezone.utc)), intervention_id, session_id, fallback_token),
            )
            await self.connection.commit()
            return cursor.rowcount == 1

    async def mark_delivery_unknown(
        self,
        intervention_id: str,
        session_id: str,
        fallback_token: str,
    ) -> bool:
        async with self._write_lock:
            cursor = await self.connection.execute(
                """UPDATE supervisor_interventions
                   SET status='DELIVERY_UNKNOWN', claim_until=NULL, claim_token=NULL,
                       fallback_until=NULL
                   WHERE id=? AND session_id=? AND status='FALLBACK_SENDING' AND fallback_token=?""",
                (intervention_id, session_id, fallback_token),
            )
            await self.connection.commit()
            return cursor.rowcount == 1
