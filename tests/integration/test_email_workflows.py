"""Integration tests for the tenant-scoped email workflow repository."""

from uuid import uuid4

import psycopg
import pytest

from orderdesk.db.email_workflows import (
    claim_for_review,
    finish_review,
    list_waiting_review,
    mark_waiting_review,
    start,
)
from orderdesk.intake.service import ingest_email
from orderdesk.storage import LocalStorage
from tests.integration.helpers import OTHER_TEST_TENANT, TEST_TENANT, fixture_bytes

pytestmark = pytest.mark.integration


def _ingest(conn: psycopg.Connection, storage: LocalStorage, tenant_id: int) -> int:
    result = ingest_email(conn, storage, tenant_id, fixture_bytes("02_garage_dubois_order_no_po.eml"))
    assert result.email_id is not None
    return result.email_id


def test_start_is_idempotent(conn: psycopg.Connection, storage: LocalStorage) -> None:
    email_id = _ingest(conn, storage, TEST_TENANT)

    first = start(conn, TEST_TENANT, email_id)
    second = start(conn, TEST_TENANT, email_id)

    assert first.email_id == second.email_id == email_id
    assert first.thread_id == second.thread_id == f"tenant-{TEST_TENANT}:email-{email_id}"


def test_claim_for_review_succeeds_once(conn: psycopg.Connection, storage: LocalStorage) -> None:
    email_id = _ingest(conn, storage, TEST_TENANT)
    start(conn, TEST_TENANT, email_id)
    mark_waiting_review(conn, TEST_TENANT, email_id, uuid4())

    claimed = claim_for_review(conn, TEST_TENANT, email_id)

    assert claimed is not None
    assert claimed.status == "reviewing"
    assert claim_for_review(conn, TEST_TENANT, email_id) is None


def test_waiting_list_is_tenant_scoped(conn: psycopg.Connection, storage: LocalStorage) -> None:
    email_id = _ingest(conn, storage, TEST_TENANT)
    other_email_id = _ingest(conn, storage, OTHER_TEST_TENANT)
    start(conn, TEST_TENANT, email_id)
    start(conn, OTHER_TEST_TENANT, other_email_id)
    mark_waiting_review(conn, TEST_TENANT, email_id, uuid4())
    mark_waiting_review(conn, OTHER_TEST_TENANT, other_email_id, uuid4())

    listed = list_waiting_review(conn, TEST_TENANT)

    assert [run.email_id for run in listed] == [email_id]
    assert all(run.tenant_id == TEST_TENANT for run in listed)


def test_finish_review_updates_status_and_reviewer(conn: psycopg.Connection, storage: LocalStorage) -> None:
    email_id = _ingest(conn, storage, TEST_TENANT)
    start(conn, TEST_TENANT, email_id)
    mark_waiting_review(conn, TEST_TENANT, email_id, uuid4())
    assert claim_for_review(conn, TEST_TENANT, email_id) is not None

    finish_review(conn, TEST_TENANT, email_id, "approved", "reviewer-1", "DRAFT-0001")
    finished = start(conn, TEST_TENANT, email_id)

    assert (finished.status, finished.reviewer, finished.erp_quotation_id) == (
        "approved",
        "reviewer-1",
        "DRAFT-0001",
    )
