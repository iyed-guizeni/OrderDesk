
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_MIGRATION_LOCK_ID = 7_310_001


def pending_migrations(conn: psycopg.Connection, folder: Path = MIGRATIONS_DIR) -> list[Path]:
    """SQL files not yet applied, in name order (0001_..., 0002_..., ...)."""
    _ensure_migrations_table(conn)
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    return [path for path in sorted(folder.glob("*.sql")) if path.stem not in applied]


def apply_migrations(conn: psycopg.Connection, folder: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every pending migration. Returns the names of the files applied."""
    applied_now: list[str] = []
    # Only one process may migrate at a time (e.g. two workers starting together).
    conn.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK_ID,))
    try:
        for path in pending_migrations(conn, folder):
            with conn.transaction():  # the file and its record are saved together, or not at all
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
            applied_now.append(path.name)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK_ID,))
    return applied_now


def _ensure_migrations_table(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     TEXT         PRIMARY KEY,          -- file name without .sql, e.g. 0001_init
            applied_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
        """
    )