"""The graph wiring: classification routes to the right branch, and branch updates fit WorkflowState."""

from orderdesk.catalogue.memory import MemoryCatalogue
from orderdesk.domain.catalogue import Product
from orderdesk.domain.classification import EmailClassification
from orderdesk.domain.extraction import ExtractedLine, ExtractedOrder, ProductInfo
from orderdesk.workflow.graph import build_graph
from tests.factories import make_email
from tests.fakes import FakeLLM, FakeStorage

PROFILE = "ACME Robinetterie sells taps and shower systems."
CATALOGUE = MemoryCatalogue([Product(code="PF60375", name="Mitigeur lavabo", family="Mitigeur lavabo")])


def classified_as(label: str) -> FakeLLM:
    return FakeLLM(EmailClassification(evidence="2 mitigeurs Profilo", label=label, confidence="high"))


def make_graph(classify_llm: FakeLLM, extract_llm: FakeLLM):
    return build_graph(classify_llm, extract_llm, PROFILE, FakeStorage(), CATALOGUE, CATALOGUE, CATALOGUE)


def test_new_order_goes_through_extract_then_resolve() -> None:
    line = ExtractedLine(raw_text="PF60375 x 2", quantity=2, product=ProductInfo(kind="named", code_as_written="PF60375"))
    extracted = ExtractedOrder(po_number="4471", lines=[line])
    graph = make_graph(classified_as("new_order"), FakeLLM(extracted))

    result = graph.invoke({"email": make_email()})

    assert result["extracted_order"] == extracted
    assert result["resolved_order"].lines[0].product.code == "PF60375"


def test_other_labels_skip_extract() -> None:
    extract_llm = FakeLLM(ExtractedOrder())
    graph = make_graph(classified_as("quote_request"), extract_llm)

    result = graph.invoke({"email": make_email()})

    assert extract_llm.calls == []
    assert "resolved_order" not in result
