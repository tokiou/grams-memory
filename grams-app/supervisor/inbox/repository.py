"""SQL queries for the durable event journal."""

from datetime import datetime, timedelta, timezone
import json
import uuid
import asyncio

import aiosqlite
import sqlite3

from supervisor.inbox.model import EventStatus, SupervisorEvent, SupervisorEventInput, utcnow


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _date(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class InboxRepository:
    def __init__(self, connection: aiosqlite.Connection, *, max_attempts: int = 3) -> None:
        self.connection = connection
        self.max_attempts = max_attempts
        self._write_lock = asyncio.Lock()

    async def initialize(self) -> None:
        existing = await (await self.connection.execute("PRAGMA table_info(supervisor_events)")).fetchall()
        legacy_rows = []
        if existing:
            existing_columns = {row[1] for row in existing}
            if "event_type" in existing_columns or "payload_json" in existing_columns:
                legacy_rows = await (await self.connection.execute("SELECT * FROM supervisor_events")).fetchall()
                await self.connection.execute("ALTER TABLE supervisor_events RENAME TO supervisor_events_legacy")
        await self.connection.execute(
            """CREATE TABLE IF NOT EXISTS supervisor_events (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                root_session_id TEXT NOT NULL,
                type TEXT NOT NULL,
                source_event TEXT,
                payload TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('PENDING', 'PROCESSING', 'PROCESSED', 'FAILED')),
                received_at TEXT NOT NULL,
                available_at TEXT NOT NULL,
                processing_at TEXT,
                 processed_at TEXT,
                 run_id TEXT,
                 source_run_id TEXT,
                 instance_id TEXT,
                 sequence INTEGER,
                 ingress_id TEXT,
                 retry_count INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                lease_id TEXT,
                lease_until TEXT
            )"""
        )
        for row in legacy_rows:
            keys = set(row.keys())
            received = row["received_at"] if "received_at" in keys and row["received_at"] else _stamp(utcnow())
            status = row["status"] if "status" in keys else EventStatus.PENDING.value
            status = EventStatus.PROCESSED.value if status == "DONE" else status
            if status not in {item.value for item in EventStatus}:
                status = EventStatus.PENDING.value
            payload = row["payload_json"] if "payload_json" in keys else row["payload"]
            await self.connection.execute(
                """INSERT OR IGNORE INTO supervisor_events
                (id, session_id, root_session_id, type, source_event, payload, status, received_at, available_at,
                 processing_at, processed_at, retry_count, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row["id"], row["session_id"] if "session_id" in keys else None,
                 row["root_session_id"] if "root_session_id" in keys and row["root_session_id"] else "default",
                 row["event_type"] if "event_type" in keys else row["type"],
                 row["source_event"] if "source_event" in keys else None, payload, status, received,
                 received, row["processing_at"] if "processing_at" in keys else None,
                 row["processed_at"] if "processed_at" in keys else None,
                 row["attempts"] if "attempts" in keys else row["retry_count"] if "retry_count" in keys else 0,
                 row["last_error"] if "last_error" in keys else row["error"] if "error" in keys else None),
            )
        if legacy_rows:
            await self.connection.execute("DROP TABLE supervisor_events_legacy")
        columns = {row[1] for row in await (await self.connection.execute("PRAGMA table_info(supervisor_events)")).fetchall()}
        migrations = {
            "root_session_id": "TEXT NOT NULL DEFAULT 'default'",
            "type": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
            "source_event": "TEXT",
            "payload": "TEXT NOT NULL DEFAULT 'null'",
            "received_at": "TEXT NOT NULL DEFAULT ''",
            "available_at": "TEXT NOT NULL DEFAULT ''",
            "processing_at": "TEXT",
            "processed_at": "TEXT",
            "run_id": "TEXT",
            "source_run_id": "TEXT",
            "instance_id": "TEXT",
            "sequence": "INTEGER",
            "ingress_id": "TEXT",
            "retry_count": "INTEGER NOT NULL DEFAULT 0",
            "error": "TEXT",
            "lease_id": "TEXT",
            "lease_until": "TEXT",
        }
        for name, definition in migrations.items():
            if name not in columns:
                await self.connection.execute(f"ALTER TABLE supervisor_events ADD COLUMN {name} {definition}")
        await self.connection.execute("UPDATE supervisor_events SET available_at = received_at WHERE available_at = ''")
        await self.connection.execute("UPDATE supervisor_events SET status = 'PROCESSED' WHERE status = 'DONE'")
        await self.connection.execute("CREATE INDEX IF NOT EXISTS idx_events_pending ON supervisor_events(status, root_session_id, received_at)")
        await self.connection.execute("CREATE INDEX IF NOT EXISTS idx_events_lease ON supervisor_events(status, lease_until)")
        await self.connection.commit()

    async def insert_event(self, event: SupervisorEventInput) -> str:
        async with self._write_lock:
            event_id = event.durable_id()
            received_at = event.received_at or utcnow()
            try:
                await self.connection.execute(
                    """INSERT INTO supervisor_events
                (id, session_id, root_session_id, type, source_event, payload, status, received_at,
                 available_at, run_id, source_run_id, instance_id, sequence, ingress_id)
                VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, NULL, ?, ?, ?, ?)""",
                    (event_id, event.session_id, event.root_session_id or "default", event.type,
                     event.source_event, json.dumps(event.payload, ensure_ascii=False, allow_nan=False), _stamp(received_at),
                     _stamp(received_at), event.run_id, event.instance_id, event.sequence, event.ingress_id),
                )
            except sqlite3.IntegrityError:
                event_id = str(uuid.uuid4())
                await self.connection.execute(
                    """INSERT INTO supervisor_events
                    (id, session_id, root_session_id, type, source_event, payload, status, received_at,
                     available_at, run_id, source_run_id, instance_id, sequence, ingress_id)
                    VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, NULL, ?, ?, ?, ?)""",
                    (event_id, event.session_id, event.root_session_id or "default", event.type,
                     event.source_event, json.dumps(event.payload, ensure_ascii=False, allow_nan=False), _stamp(received_at),
                     _stamp(received_at), event.run_id, event.instance_id, event.sequence, event.ingress_id),
                )
            await self.connection.commit()
            return event_id

    async def get_event(self, event_id: str) -> SupervisorEvent | None:
        row = await (await self.connection.execute("SELECT * FROM supervisor_events WHERE id = ?", (event_id,))).fetchone()
        return self._row(row) if row else None

    async def list_pending(self, root_session_id: str | None = None, limit: int | None = None) -> list[SupervisorEvent]:
        query = "SELECT * FROM supervisor_events WHERE status = 'PENDING' AND available_at <= ?"
        params: list[object] = [_stamp(utcnow())]
        if root_session_id is not None:
            query += " AND root_session_id = ?"
            params.append(root_session_id)
        query += " ORDER BY received_at, rowid"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = await (await self.connection.execute(query, params)).fetchall()
        return [self._row(row) for row in rows]

    async def list_unfinished(self) -> list[SupervisorEvent]:
        rows = await (await self.connection.execute("SELECT * FROM supervisor_events WHERE status IN ('PENDING', 'PROCESSING') ORDER BY received_at, rowid")).fetchall()
        return [self._row(row) for row in rows]

    async def count_pending(self, root_session_id: str | None = None) -> int:
        if root_session_id is None:
            row = await (await self.connection.execute("SELECT COUNT(*) FROM supervisor_events WHERE status = 'PENDING' AND available_at <= ?", (_stamp(utcnow()),))).fetchone()
        else:
            row = await (await self.connection.execute("SELECT COUNT(*) FROM supervisor_events WHERE status = 'PENDING' AND available_at <= ? AND root_session_id = ?", (_stamp(utcnow()), root_session_id))).fetchone()
        return int(row[0])

    async def pending_roots(self) -> list[str]:
        rows = await (await self.connection.execute("SELECT DISTINCT root_session_id FROM supervisor_events WHERE status = 'PENDING' AND available_at <= ? ORDER BY root_session_id", (_stamp(utcnow()),))).fetchall()
        return [str(row[0]) for row in rows]

    async def known_roots(self) -> list[str]:
        rows = await (await self.connection.execute(
            "SELECT DISTINCT root_session_id FROM supervisor_events ORDER BY root_session_id",
        )).fetchall()
        return [str(row[0]) for row in rows]

    async def claimable_roots(self) -> list[str]:
        now = _stamp(utcnow())
        rows = await (await self.connection.execute(
            """SELECT DISTINCT root_session_id FROM supervisor_events
               WHERE (status='PENDING' AND available_at <= ?)
                  OR (status='PROCESSING' AND (lease_id IS NULL OR lease_until <= ?))
               ORDER BY root_session_id""",
            (now, now),
        )).fetchall()
        return [str(row[0]) for row in rows]

    async def claim_pending(self, root_session_id: str, limit: int, lease_seconds: float, *, run_id: str | None = None) -> list[SupervisorEvent]:
        async with self._write_lock:
            now = utcnow()
            lease_until = now + timedelta(seconds=lease_seconds)
            now_stamp = _stamp(now)
            await self.connection.execute("BEGIN IMMEDIATE")
            try:
                rows = await (await self.connection.execute(
                    """SELECT * FROM supervisor_events WHERE root_session_id = ? AND
                       ((status = 'PENDING' AND available_at <= ?) OR
                        (status = 'PROCESSING' AND (lease_id IS NULL OR lease_until <= ?)))
                       ORDER BY received_at, rowid LIMIT ?""",
                    (root_session_id, now_stamp, now_stamp, limit),
                )).fetchall()
                claimed: list[SupervisorEvent] = []
                for row in rows:
                    lease_id = str(uuid.uuid4())
                    await self.connection.execute(
                        """UPDATE supervisor_events SET status='PROCESSING', processing_at=?, retry_count=retry_count+1,
                           lease_id=?, lease_until=?, run_id=? WHERE id=?""",
                        (now_stamp, lease_id, _stamp(lease_until), run_id, row[0]),
                    )
                    updated = await self.get_event(row[0])
                    if updated:
                        claimed.append(updated)
                await self.connection.commit()
                return claimed
            except BaseException:
                await self.connection.rollback()
                raise

    async def mark_processing(self, event_id: str, *, run_id: str | None = None) -> bool:
        now = _stamp(utcnow())
        cursor = await self.connection.execute(
            "UPDATE supervisor_events SET status='PROCESSING', processing_at=?, retry_count=retry_count+1, run_id=? WHERE id=? AND status='PENDING'",
            (now, run_id, event_id),
        )
        await self.connection.commit()
        return cursor.rowcount == 1

    async def mark_processed(self, event_id: str, lease_id: str | None = None) -> bool:
        processed_at = _stamp(utcnow())
        if not lease_id:
            row = await self.get_event(event_id)
            return row is not None and row.status is EventStatus.PROCESSED
        cursor = await self.connection.execute(
            "UPDATE supervisor_events SET status='PROCESSED', processed_at=?, lease_id=NULL, lease_until=NULL WHERE id=? AND status='PROCESSING' AND lease_id=?",
            (processed_at, event_id, lease_id),
        )
        await self.connection.commit()
        if cursor.rowcount:
            return True
        row = await self.get_event(event_id)
        return row is not None and row.status is EventStatus.PROCESSED

    async def mark_failed(self, event_id: str, error: str, retry_at: datetime | None = None, lease_id: str | None = None) -> EventStatus | None:
        row = await self.get_event(event_id)
        if row is None or row.status is not EventStatus.PROCESSING:
            return row.status if row else None
        if not lease_id or row.lease_id != lease_id:
            return row.status
        status = EventStatus.FAILED if row.retry_count >= self.max_attempts else EventStatus.PENDING
        available = retry_at or (utcnow() + timedelta(seconds=min(2.0, 0.05 * (2 ** max(0, row.retry_count - 1)))))
        predicate = "id = ? AND status = 'PROCESSING'"
        predicate += " AND lease_id = ?"
        await self.connection.execute(
            f"UPDATE supervisor_events SET status=?, available_at=?, error=?, lease_until=NULL, lease_id=NULL WHERE {predicate}",
            (status.value, _stamp(available), error, event_id, *([lease_id] if lease_id else [])),
        )
        await self.connection.commit()
        return status

    async def recover_unfinished(self) -> None:
        await self.recover_expired(utcnow())

    async def recover_expired(self, now: datetime) -> None:
        async with self._write_lock:
            rows = await self.list_unfinished()
            now_stamp = _stamp(now)
            for row in rows:
                if row.status is not EventStatus.PROCESSING:
                    continue
                if row.lease_until is not None and _stamp(row.lease_until) > now_stamp:
                    continue
                status = EventStatus.FAILED if row.retry_count >= self.max_attempts else EventStatus.PENDING
                await self.connection.execute(
                    """UPDATE supervisor_events
                       SET status=?, error=?, lease_id=NULL, lease_until=NULL
                       WHERE id=? AND status='PROCESSING' AND lease_id IS ?
                         AND (lease_until IS NULL OR lease_until <= ?)""",
                    (status.value, "processing recovered after lease expiry", row.id, row.lease_id, now_stamp),
                )
            await self.connection.commit()

    async def processing_for_root(self, root_session_id: str) -> list[SupervisorEvent]:
        rows = await (await self.connection.execute("SELECT * FROM supervisor_events WHERE status='PROCESSING' AND root_session_id=?", (root_session_id,))).fetchall()
        return [self._row(row) for row in rows]

    def _row(self, row: aiosqlite.Row) -> SupervisorEvent:
        return SupervisorEvent(
            id=row["id"], session_id=row["session_id"], root_session_id=row["root_session_id"],
            type=row["type"], source_event=row["source_event"], payload=json.loads(row["payload"]),
            status=EventStatus(row["status"]), received_at=_date(row["received_at"]) or utcnow(),
            processing_at=_date(row["processing_at"]), processed_at=_date(row["processed_at"]),
            run_id=row["run_id"], source_run_id=row["source_run_id"], instance_id=row["instance_id"], sequence=row["sequence"],
            retry_count=row["retry_count"], error=row["error"], lease_id=row["lease_id"],
            lease_until=_date(row["lease_until"]), ingress_id=row["ingress_id"],
        )
