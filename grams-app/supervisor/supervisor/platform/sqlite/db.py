"""SQLite setup for the event journal and LangGraph checkpoints."""

from pathlib import Path
from typing import Any

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


async def open_connection(path: Path) -> aiosqlite.Connection:
    path.expanduser().parent.mkdir(parents=True, exist_ok=True)
    connection = await aiosqlite.connect(path.expanduser(), timeout=30)
    try:
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA journal_mode=WAL")
        await connection.execute("PRAGMA foreign_keys=ON")
        await connection.execute("PRAGMA busy_timeout=5000")
        await connection.commit()
        return connection
    except BaseException:
        await connection.close()
        raise


async def open_checkpointer(path: Path, connection: aiosqlite.Connection | None = None) -> tuple[AsyncSqliteSaver, Any]:
    """Open the persistent checkpointer, reusing the journal connection when available."""
    if connection is not None:
        saver = AsyncSqliteSaver(connection)
        await saver.setup()
        return saver, None
    context = AsyncSqliteSaver.from_conn_string(str(path.expanduser()))
    try:
        saver = await context.__aenter__()
        await saver.conn.execute("PRAGMA journal_mode=WAL")
        await saver.conn.execute("PRAGMA busy_timeout=30000")
        await saver.conn.commit()
        await saver.setup()
        return saver, context
    except BaseException:
        await context.__aexit__(None, None, None)
        raise


async def close_checkpointer(context: Any, error: BaseException | None = None) -> None:
    await context.__aexit__(None if error is None else type(error), error, None)
