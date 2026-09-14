"""SQLite setup for the durable event journal."""

from pathlib import Path
import aiosqlite


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
