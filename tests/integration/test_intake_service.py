"""Integration tests for the intake service: real PostgreSQL, local file storage.

Run with:  uv run pytest -m integration
"""

import psycopg
import pytest
from psycopg import sql

from orderdesk.intake.service import ingest_email
from orderdesk.storage import LocalStorage
from tests.integration.helpers import FIXTURES, TEST_TENANT, fixture_bytes

pytestmark = pytest.mark.integration


def count(conn: psycopg.Connection, table: str) -> int:
    query = sql.SQL("SELECT count(*) FROM {} WHERE tenant_id = %s").format(sql.Identifier(table))
    row = conn.execute(query, (TEST_TENANT,)).fetchone()
    assert row is not None
    return row[0]


def test_new_email_is_stored_with_its_attachment(conn: psycopg.Connection, storage: LocalStorage) -> None:
    result = ingest_email(conn, storage, TEST_TENANT, fixture_bytes("01_karim_order_BM-2291.eml"))

    assert result.status == "new"
    row = conn.execute(
        "SELECT from_address, subject, raw_key FROM emails WHERE id = %s", (result.email_id,)
    ).fetchone()
    assert row is not None
    assert row[0] == "karim@btp-martin.fr"
    assert row[1] == "Commande BM-2291 - livraison jeudi"
    assert storage.get(row[2]) == fixture_bytes("01_karim_order_BM-2291.eml")

    attachments = conn.execute(
        "SELECT filename, storage_key FROM attachments WHERE email_id = %s", (result.email_id,)
    ).fetchall()
    assert [a[0] for a in attachments] == ["BC_BM-2291.csv"]
    assert b"rondelles M8" in storage.get(attachments[0][1])


def test_new_email_gets_one_pending_job(conn: psycopg.Connection, storage: LocalStorage) -> None:
    result = ingest_email(conn, storage, TEST_TENANT, fixture_bytes("01_karim_order_BM-2291.eml"))

    jobs = conn.execute(
        "SELECT kind, status, attempts FROM jobs WHERE email_id = %s", (result.email_id,)
    ).fetchall()
    assert jobs == [("process_email", "pending", 0)]


def test_same_email_twice_is_stored_once(conn: psycopg.Connection, storage: LocalStorage) -> None:
    first = ingest_email(conn, storage, TEST_TENANT, fixture_bytes("01_karim_order_BM-2291.eml"))
    second = ingest_email(conn, storage, TEST_TENANT, fixture_bytes("04_karim_order_duplicate.eml"))

    assert first.status == "new"
    assert second.status == "duplicate"
    assert second.email_id == first.email_id
    assert count(conn, "emails") == 1
    assert count(conn, "attachments") == 1
    assert count(conn, "jobs") == 1


def test_whole_folder(conn: psycopg.Connection, storage: LocalStorage) -> None:
    statuses = [
        ingest_email(conn, storage, TEST_TENANT, path.read_bytes()).status
        for path in sorted(FIXTURES.glob("*.eml"))
    ]

    assert statuses == ["new", "new", "new", "duplicate"]
    assert count(conn, "jobs") == 3


def test_reply_keeps_thread_headers(conn: psycopg.Connection, storage: LocalStorage) -> None:
    result = ingest_email(conn, storage, TEST_TENANT, fixture_bytes("03_karim_correction_reply.eml"))

    row = conn.execute(
        "SELECT in_reply_to, references_ids FROM emails WHERE id = %s", (result.email_id,)
    ).fetchone()
    assert row == (
        "<20261021093010.77c1@dupont-fixations.fr>",
        ["<20261021091402.4f7a@btp-martin.fr>", "<20261021093010.77c1@dupont-fixations.fr>"],
    )


def test_email_without_sender_is_rejected_and_not_stored(conn: psycopg.Connection, storage: LocalStorage) -> None:
    raw = b"Subject: no sender\r\nMessage-ID: <x@example.com>\r\n\r\nhello\r\n"

    result = ingest_email(conn, storage, TEST_TENANT, raw)

    assert result.status == "rejected"
    assert count(conn, "emails") == 0
    assert count(conn, "jobs") == 0