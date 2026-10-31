"""Integration tests for the migration runner."""

import psycopg
import pytest

from orderdesk.db.migrate import apply_migrations, pending_migrations

pytestmark = pytest.mark.integration


def test_all_migrations_are_applied_and_running_again_does_nothing(conn: psycopg.Connection) -> None:
    apply_migrations(conn)

    assert pending_migrations(conn) == []
    assert apply_migrations(conn) == []

    versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
    assert versions[:4] == ["0001_init", "0002_jobs", "0003_jobs_fairness", "0004_jobs_claimed_at"]