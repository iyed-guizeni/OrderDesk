"""Integration tests for the Postgres checkpoint, graph worker and review hand-off."""

from datetime import date
from uuid import UUID

import psycopg
import pytest
from langgraph.checkpoint.postgres import PostgresSaver

from orderdesk.catalogue.memory import MemoryCatalogue
from orderdesk.db.checkpointer import open_checkpointer
from orderdesk.db.email_workflows import get as get_workflow
from orderdesk.domain.catalogue import Customer, Product
from orderdesk.domain.classification import EmailClassification
from orderdesk.domain.extraction import ExtractedLine, ExtractedOrder, ProductInfo, RequestedDate
from orderdesk.intake.parser import parse_email
from orderdesk.intake.service import ingest_email
from orderdesk.jobs.queue import PROCESS_EMAIL, enqueue
from orderdesk.jobs.worker import HANDLERS, make_process_email, run_until_empty
from orderdesk.services.review import pending_draft, submit_review
from orderdesk.storage import LocalStorage
from orderdesk.workflow.graph import build_graph
from tests.fakes import FakeLLM
from tests.integration.helpers import TEST_TENANT, fixture_bytes

pytestmark = pytest.mark.integration

FIXTURE_NAME = "02_garage_dubois_order_no_po.eml"
PRODUCT = Product(code="TEST-1", name="Test product", family="Test", list_price=12.25)
PROFILE = "Synthetic integration-test business."


class NoAgent:
    def bind_tools(self, tools):
        raise AssertionError("the agent must not run in this integration test")


class RaisingGraph:
    def invoke(self, *args, **kwargs):
        raise RuntimeError("resume failed")


class NeverInvokedGraph:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("graph must not be invoked")


def _catalogue() -> tuple[MemoryCatalogue, str]:
    parsed = parse_email(fixture_bytes(FIXTURE_NAME))
    customer = Customer(id=51, name="Synthetic test customer")
    return MemoryCatalogue([PRODUCT], {parsed.from_address: customer}), parsed.from_address


def _graph(
    catalogue: MemoryCatalogue,
    storage: LocalStorage,
    saver: PostgresSaver,
    label: str = "new_order",
    extracted: ExtractedOrder | None = None,
    classify_llm: FakeLLM | None = None,
):
    classifier = classify_llm or FakeLLM(
        EmailClassification(evidence="synthetic order", label=label, confidence="high")
    )
    extract = FakeLLM(extracted or _order_with_product())
    return build_graph(
        classifier,
        extract,
        FakeLLM(None),
        NoAgent(),
        PROFILE,
        storage,
        catalogue,
        catalogue,
        catalogue,
        catalogue,
        catalogue,
        checkpointer=saver,
    ), classifier, extract


def _order_with_product() -> ExtractedOrder:
    return ExtractedOrder(
        requested_date=RequestedDate(text="2026-12-01", value=date(2026, 12, 1)),
        lines=[
            ExtractedLine(
                raw_text="1 test product",
                quantity=1,
                unit="piece",
                product=ProductInfo(kind="named", code_as_written=PRODUCT.code),
            )
        ],
    )


def _ingest(conn: psycopg.Connection, storage: LocalStorage) -> int:
    result = ingest_email(conn, storage, TEST_TENANT, fixture_bytes(FIXTURE_NAME))
    assert result.email_id is not None
    return result.email_id


def _close(saver: PostgresSaver) -> None:
    saver.conn.close()


def _start_worker_graph(
    conn: psycopg.Connection,
    database_url: str,
    storage: LocalStorage,
    label: str = "new_order",
    extracted: ExtractedOrder | None = None,
):
    email_id = _ingest(conn, storage)
    catalogue, _ = _catalogue()
    saver = open_checkpointer(database_url)
    graph, classifier, extract = _graph(catalogue, storage, saver, label, extracted)
    handlers = {**HANDLERS, PROCESS_EMAIL: make_process_email(graph)}
    run_until_empty(conn, "integration-worker", handlers, [TEST_TENANT])
    return email_id, catalogue, saver, graph, classifier, extract


def test_worker_pauses_new_order_with_trace_and_completes_job(
    conn: psycopg.Connection, storage: LocalStorage, database_url: str
) -> None:
    email_id, _, saver, _, _, _ = _start_worker_graph(conn, database_url, storage)
    try:
        run = get_workflow(conn, TEST_TENANT, email_id)
        job = conn.execute("SELECT status FROM jobs WHERE tenant_id = %s AND email_id = %s",
                           (TEST_TENANT, email_id)).fetchone()
        assert run is not None
        assert run.status == "waiting_review"
        assert isinstance(run.trace_id, UUID)
        assert job == ("done",)
    finally:
        _close(saver)


def test_worker_finishes_other_label_without_review_trace(
    conn: psycopg.Connection, storage: LocalStorage, database_url: str
) -> None:
    email_id, _, saver, _, _, _ = _start_worker_graph(conn, database_url, storage, label="other")
    try:
        run = get_workflow(conn, TEST_TENANT, email_id)
        assert run is not None
        assert run.status == "finished"
        assert run.trace_id is None
    finally:
        _close(saver)


def test_redelivered_waiting_job_does_not_run_llms_again(
    conn: psycopg.Connection, storage: LocalStorage, database_url: str
) -> None:
    email_id, _, saver, graph, classifier, extract = _start_worker_graph(conn, database_url, storage)
    try:
        calls_before = (len(classifier.calls), len(extract.calls))
        enqueue(conn, TEST_TENANT, email_id)
        # The unique email/kind job constraint keeps one row; reset it to model queue redelivery.
        conn.execute(
            """
            UPDATE jobs SET status = 'pending', finished_at = NULL, available_at = now()
            WHERE tenant_id = %s AND email_id = %s
            """,
            (TEST_TENANT, email_id),
        )
        run_until_empty(
            conn,
            "redelivery-worker",
            {**HANDLERS, PROCESS_EMAIL: make_process_email(graph)},
            [TEST_TENANT],
        )

        assert (len(classifier.calls), len(extract.calls)) == calls_before
        assert get_workflow(conn, TEST_TENANT, email_id).status == "waiting_review"
    finally:
        _close(saver)


def test_graph_failure_keeps_workflow_running_and_fails_job(
    conn: psycopg.Connection, storage: LocalStorage, database_url: str
) -> None:
    email_id = _ingest(conn, storage)
    catalogue, _ = _catalogue()
    saver = open_checkpointer(database_url)
    graph, _, _ = _graph(
        catalogue,
        storage,
        saver,
        classify_llm=FakeLLM(RuntimeError("synthetic model failure")),
    )
    try:
        run_until_empty(conn, "failing-worker", {**HANDLERS, PROCESS_EMAIL: make_process_email(graph)}, [TEST_TENANT])
        run = get_workflow(conn, TEST_TENANT, email_id)
        job = conn.execute("SELECT status FROM jobs WHERE tenant_id = %s AND email_id = %s",
                           (TEST_TENANT, email_id)).fetchone()
        assert run is not None and run.status == "running"
        assert job == ("pending",)
    finally:
        _close(saver)


def test_postgres_checkpoint_resumes_from_a_second_graph(
    conn: psycopg.Connection, storage: LocalStorage, database_url: str
) -> None:
    email_id, catalogue, worker_saver, _, _, _ = _start_worker_graph(conn, database_url, storage)
    reviewer_saver = open_checkpointer(database_url)
    try:
        reviewer_graph, _, _ = _graph(catalogue, storage, reviewer_saver)
        run = get_workflow(conn, TEST_TENANT, email_id)
        assert run is not None
        pending = pending_draft(reviewer_graph, conn, TEST_TENANT, email_id)
        assert pending is not None
        draft, errors = pending
        assert errors == []

        result = submit_review(
            reviewer_graph,
            conn,
            TEST_TENANT,
            email_id,
            {"action": "approve", "reviewer": "integration-reviewer"},
        )

        assert result.outcome == "approved"
        snapshot = reviewer_graph.get_state({"configurable": {"thread_id": run.thread_id}})
        assert snapshot.values["approved_order"] == draft
        assert snapshot.values["extracted_order"].requested_date.value == date(2026, 12, 1)
        updated = get_workflow(conn, TEST_TENANT, email_id)
        assert updated is not None and updated.status == "approved"
        assert updated.erp_quotation_id is not None
        assert conn.execute(
            "SELECT status FROM jobs WHERE tenant_id = %s AND email_id = %s",
            (TEST_TENANT, email_id),
        ).fetchone() == ("done",)
    finally:
        _close(worker_saver)
        _close(reviewer_saver)


def test_reject_does_not_write_a_quotation(
    conn: psycopg.Connection, storage: LocalStorage, database_url: str
) -> None:
    email_id, catalogue, worker_saver, _, _, _ = _start_worker_graph(conn, database_url, storage)
    reviewer_saver = open_checkpointer(database_url)
    try:
        reviewer_graph, _, _ = _graph(catalogue, storage, reviewer_saver)
        result = submit_review(
            reviewer_graph, conn, TEST_TENANT, email_id, {"action": "reject", "reviewer": "integration-reviewer"}
        )
        updated = get_workflow(conn, TEST_TENANT, email_id)

        assert result.outcome == "rejected"
        assert updated is not None and updated.status == "rejected"
        assert updated.erp_quotation_id is None
        assert catalogue.draft_quotations == {}
    finally:
        _close(worker_saver)
        _close(reviewer_saver)


def test_refused_review_can_be_corrected_in_a_later_submission(
    conn: psycopg.Connection, storage: LocalStorage, database_url: str
) -> None:
    order = ExtractedOrder(lines=[
        _order_with_product().lines[0],
        ExtractedLine(raw_text="another item", quantity=1, product=ProductInfo(kind="missing")),
    ])
    email_id, catalogue, worker_saver, _, _, _ = _start_worker_graph(
        conn, database_url, storage, extracted=order
    )
    reviewer_saver = open_checkpointer(database_url)
    try:
        reviewer_graph, _, _ = _graph(catalogue, storage, reviewer_saver)
        refused = submit_review(
            reviewer_graph, conn, TEST_TENANT, email_id, {"action": "approve", "reviewer": "reviewer"}
        )
        assert refused.outcome == "asked_again"
        assert refused.errors == ["line 1 has no product: choose one or remove it"]
        assert get_workflow(conn, TEST_TENANT, email_id).status == "waiting_review"

        accepted = submit_review(
            reviewer_graph,
            conn,
            TEST_TENANT,
            email_id,
            {
                "action": "approve",
                "reviewer": "reviewer",
                "changes": [{"line_index": 1, "remove": True}],
            },
        )
        assert accepted.outcome == "approved"
    finally:
        _close(worker_saver)
        _close(reviewer_saver)


def test_review_not_waiting_does_not_invoke_graph(conn: psycopg.Connection, storage: LocalStorage) -> None:
    email_id = _ingest(conn, storage)
    graph = NeverInvokedGraph()

    result = submit_review(graph, conn, TEST_TENANT, email_id, {"action": "approve"})

    assert result.outcome == "not_waiting"
    assert graph.calls == 0


def test_resume_error_restores_waiting_status_and_propagates(
    conn: psycopg.Connection, storage: LocalStorage, database_url: str
) -> None:
    email_id, _, saver, _, _, _ = _start_worker_graph(conn, database_url, storage)
    try:
        with pytest.raises(RuntimeError, match="resume failed"):
            submit_review(RaisingGraph(), conn, TEST_TENANT, email_id, {"action": "approve"})
        run = get_workflow(conn, TEST_TENANT, email_id)
        assert run is not None and run.status == "waiting_review"
    finally:
        _close(saver)
