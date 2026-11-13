"""The graph wiring: classification routes to the right branch, and branch updates fit WorkflowState."""

from orderdesk.catalogue.memory import MemoryCatalogue
from orderdesk.domain.catalogue import Product
from orderdesk.domain.classification import EmailClassification
from orderdesk.domain.extraction import ExtractedLine, ExtractedOrder, ProductInfo
from orderdesk.domain.resolution import CandidateChoice
from orderdesk.workflow.graph import build_graph
from tests.factories import make_email
from tests.fakes import FakeLLM, FakeStorage

PROFILE = "ACME Robinetterie sells taps and shower systems."
CATALOGUE = MemoryCatalogue([
    Product(code="PF60375", name="Mitigeur lavabo", family="Mitigeur lavabo"),
    Product(code="PF60380", name="Mitigeur bain-douche", family="Mitigeur bain-douche"),
])


def classified_as(label: str) -> FakeLLM:
    return FakeLLM(EmailClassification(evidence="2 mitigeurs Profilo", label=label, confidence="high"))


def make_graph(classify_llm: FakeLLM, extract_llm: FakeLLM, choose_llm: FakeLLM | None = None):
    choose_llm = choose_llm or FakeLLM(None)  # fails if called: most tests must not reach the choice step
    return build_graph(classify_llm, extract_llm, choose_llm, PROFILE, FakeStorage(), CATALOGUE, CATALOGUE, CATALOGUE)


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


def test_ambiguous_lines_go_through_the_llm_choice() -> None:
    line = ExtractedLine(raw_text="2 mitigeurs", quantity=2, product=ProductInfo(kind="named", text="mitigeurs"))
    choose_llm = FakeLLM(CandidateChoice(evidence="salle de bain", code="PF60380"))
    graph = make_graph(classified_as("new_order"), FakeLLM(ExtractedOrder(lines=[line])), choose_llm)

    result = graph.invoke({"email": make_email()})

    assert len(choose_llm.calls) == 1
    assert result["resolved_order"].lines[0].method == "llm_choice"
