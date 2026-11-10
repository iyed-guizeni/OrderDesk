"""The graph wiring: classification routes to the right branch, and branch updates fit WorkflowState."""

from orderdesk.domain.classification import EmailClassification
from orderdesk.domain.extraction import ExtractedOrder
from orderdesk.workflow.graph import build_graph
from tests.factories import make_email
from tests.fakes import FakeLLM, FakeStorage

PROFILE = "ACME Robinetterie sells taps and shower systems."


def classified_as(label: str) -> FakeLLM:
    return FakeLLM(EmailClassification(evidence="2 mitigeurs Profilo", label=label, confidence="high"))


def test_new_order_goes_through_extract() -> None:
    extracted = ExtractedOrder(po_number="4471")
    graph = build_graph(classified_as("new_order"), FakeLLM(extracted), PROFILE, FakeStorage())

    result = graph.invoke({"email": make_email()})

    assert result["extracted_order"] == extracted


def test_other_labels_skip_extract() -> None:
    extract_llm = FakeLLM(ExtractedOrder())
    graph = build_graph(classified_as("quote_request"), extract_llm, PROFILE, FakeStorage())

    graph.invoke({"email": make_email()})

    assert extract_llm.calls == []
