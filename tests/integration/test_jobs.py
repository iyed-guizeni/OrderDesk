

from datetime import timedelta

import psycopg
import pytest

from orderdesk.db.connection import connect
from orderdesk.intake.service import ingest_email
from orderdesk.jobs.queue import Job, claim, enqueue, retry_delay
from orderdesk.jobs.worker import HANDLERS, run_one, run_until_empty
from orderdesk.storage import LocalStorage
from tests.integration.helpers import FIXTURES, OTHER_TEST_TENANT, TEST_TENANT, TEST_TENANTS, fixture_bytes

pytestmark = pytest.mark.integration

ONLY_TEST = [TEST_TENANT]


def ingest_all(conn: psycopg.Connection, storage: LocalStorage, tenant_id: int = TEST_TENANT) -> list[int]:
    """Ingest the sample emails for one company; return the ids of the new ones."""
    ids = []
    for path in sorted(FIXTURES.glob("*.eml")):
        result = ingest_email(conn, storage, tenant_id, path.read_bytes())
        if result.status == "new" and result.email_id is not None:
            ids.append(result.email_id)
    return ids


def job_rows(conn: psycopg.Connection, tenant_id: int = TEST_TENANT) -> list[tuple[str, int]]:
    return conn.execute(
        "SELECT status, attempts FROM jobs WHERE tenant_id = %s ORDER BY id", (tenant_id,)
    ).fetchall()


def make_retries_available_now(conn: psycopg.Connection) -> None:
    """Pretend the retry delay is over (instead of really waiting 30 seconds)."""
    conn.execute("UPDATE jobs SET available_at = now() WHERE tenant_id = ANY(%s)", (TEST_TENANTS,))


# ---------- basics ----------

def test_enqueue_twice_creates_one_job(conn: psycopg.Connection, storage: LocalStorage) -> None:
    result = ingest_email(conn, storage, TEST_TENANT, fixture_bytes("02_garage_dubois_order_no_po.eml"))
    assert result.email_id is not None

    enqueue(conn, TEST_TENANT, result.email_id)  # already created by ingest_email

    assert len(job_rows(conn)) == 1


def test_claim_takes_oldest_job_and_marks_it_running(conn: psycopg.Connection, storage: LocalStorage) -> None:
    email_ids = ingest_all(conn, storage)

    job = claim(conn, "worker-A", tenant_ids=ONLY_TEST)

    assert job is not None
    assert job.email_id == email_ids[0]
    assert job.attempts == 1
    row = conn.execute("SELECT status, locked_by FROM jobs WHERE id = %s", (job.id,)).fetchone()
    assert row == ("running", "worker-A")


def test_two_workers_never_get_the_same_job(
    conn: psycopg.Connection, storage: LocalStorage, database_url: str
) -> None:
    ingest_all(conn, storage)

    with connect(database_url) as other_conn:
        # Worker A is in the middle of taking job 1: it holds a lock on that row.
        with conn.transaction():
            job_a = claim(conn, "worker-A", tenant_ids=ONLY_TEST)
            # Meanwhile worker B asks for a job: SKIP LOCKED makes it take the next one.
            job_b = claim(other_conn, "worker-B", tenant_ids=ONLY_TEST)

    assert job_a is not None and job_b is not None
    assert job_a.id != job_b.id


def test_worker_processes_every_job(conn: psycopg.Connection, storage: LocalStorage) -> None:
    ingest_all(conn, storage)

    processed = run_until_empty(conn, "worker-A", tenant_ids=ONLY_TEST)

    assert processed == 3
    assert job_rows(conn) == [("done", 1), ("done", 1), ("done", 1)]


# ---------- failures, retries and crashes ----------

def test_failed_job_waits_before_retry(conn: psycopg.Connection, storage: LocalStorage) -> None:
    ingest_email(conn, storage, TEST_TENANT, fixture_bytes("02_garage_dubois_order_no_po.eml"))

    def always_fails(_conn: psycopg.Connection, _job: Job) -> None:
        raise RuntimeError("Odoo is down")

    handlers = {**HANDLERS, "process_email": always_fails}
    assert run_one(conn, "worker-A", handlers, ONLY_TEST)

    assert job_rows(conn) == [("pending", 1)]                          # back in the queue...
    assert claim(conn, "worker-A", tenant_ids=ONLY_TEST) is None       # ...but not before its delay
    delay = conn.execute(
        "SELECT available_at - now() FROM jobs WHERE tenant_id = %s", (TEST_TENANT,)
    ).fetchone()
    assert delay is not None and timedelta(seconds=25) < delay[0] <= timedelta(seconds=30)


def test_failing_job_is_retried_then_marked_failed(conn: psycopg.Connection, storage: LocalStorage) -> None:
    ingest_email(conn, storage, TEST_TENANT, fixture_bytes("02_garage_dubois_order_no_po.eml"))

    def always_fails(_conn: psycopg.Connection, _job: Job) -> None:
        raise RuntimeError("Odoo is down")

    handlers = {**HANDLERS, "process_email": always_fails}
    for _ in range(3):
        make_retries_available_now(conn)
        assert run_one(conn, "worker-A", handlers, ONLY_TEST)

    assert job_rows(conn) == [("failed", 3)]  # gave up after 3 attempts
    error = conn.execute("SELECT last_error FROM jobs WHERE tenant_id = %s", (TEST_TENANT,)).fetchone()
    assert error == ("RuntimeError: Odoo is down",)


def test_retry_delay_grows() -> None:
    assert retry_delay(1) == timedelta(seconds=30)
    assert retry_delay(2) == timedelta(minutes=2)
    assert retry_delay(3) == timedelta(minutes=10)
    assert retry_delay(9) == timedelta(minutes=10)  # never more than the last delay


def test_job_of_crashed_worker_is_taken_over(conn: psycopg.Connection, storage: LocalStorage) -> None:
    ingest_email(conn, storage, TEST_TENANT, fixture_bytes("02_garage_dubois_order_no_po.eml"))
    first = claim(conn, "worker-A", tenant_ids=ONLY_TEST)
    assert first is not None

    # Worker A "crashed" an hour ago without finishing.
    conn.execute("UPDATE jobs SET locked_at = now() - interval '1 hour' WHERE id = %s", (first.id,))
    second = claim(conn, "worker-B", tenant_ids=ONLY_TEST)

    assert second is not None
    assert second.id == first.id
    assert second.attempts == 2


def test_running_job_of_live_worker_is_not_taken(conn: psycopg.Connection, storage: LocalStorage) -> None:
    ingest_email(conn, storage, TEST_TENANT, fixture_bytes("02_garage_dubois_order_no_po.eml"))
    assert claim(conn, "worker-A", tenant_ids=ONLY_TEST) is not None

    assert claim(conn, "worker-B", tenant_ids=ONLY_TEST) is None


# ---------- fairness between companies ----------

def test_companies_take_turns(conn: psycopg.Connection, storage: LocalStorage) -> None:
    ingest_all(conn, storage, TEST_TENANT)                                    # company 999: 3 jobs first
    ingest_email(conn, storage, OTHER_TEST_TENANT, fixture_bytes("02_garage_dubois_order_no_po.eml"))  # 998: 1 job

    first = claim(conn, "worker-A", tenant_ids=TEST_TENANTS)
    second = claim(conn, "worker-B", tenant_ids=TEST_TENANTS)

    assert first is not None and second is not None
    assert first.tenant_id == TEST_TENANT
    assert second.tenant_id == OTHER_TEST_TENANT  # served 2nd, not 4th


def test_single_worker_alternates_between_companies(conn: psycopg.Connection, storage: LocalStorage) -> None:
    ingest_all(conn, storage, TEST_TENANT)                                    # company 999: 3 jobs first
    ingest_email(conn, storage, OTHER_TEST_TENANT, fixture_bytes("02_garage_dubois_order_no_po.eml"))  # 998: 1 job
    served: list[int] = []

    def record(_conn: psycopg.Connection, job: Job) -> None:
        served.append(job.tenant_id)

    run_until_empty(conn, "worker-A", {**HANDLERS, "process_email": record}, TEST_TENANTS)

    # Company 998 is served 2nd, not after all of company 999's jobs.
    assert served == [TEST_TENANT, OTHER_TEST_TENANT, TEST_TENANT, TEST_TENANT]


def test_company_at_its_limit_is_skipped(conn: psycopg.Connection, storage: LocalStorage) -> None:
    ingest_all(conn, storage, TEST_TENANT)  # 3 jobs for company 999

    claimed = [claim(conn, f"worker-{n}", tenant_ids=ONLY_TEST, max_running=2) for n in range(3)]

    assert claimed[0] is not None and claimed[1] is not None
    assert claimed[2] is None  # a 3rd job would exceed the limit of 2: wait


def test_abandoned_jobs_do_not_count_against_the_limit(conn: psycopg.Connection, storage: LocalStorage) -> None:
    ingest_all(conn, storage, TEST_TENANT)
    for n in range(2):
        assert claim(conn, f"worker-{n}", tenant_ids=ONLY_TEST, max_running=2) is not None

    # Both workers crashed long ago: their jobs must still be taken over (no deadlock).
    conn.execute("UPDATE jobs SET locked_at = now() - interval '1 hour' WHERE status = 'running'")
    rescued = claim(conn, "worker-rescue", tenant_ids=ONLY_TEST, max_running=2)

    assert rescued is not None
    assert rescued.attempts == 2