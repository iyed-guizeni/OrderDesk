"""Tests for safe product synonym learning after human approval."""

from orderdesk.catalogue.memory import MemoryCatalogue
from orderdesk.checks.order import aliases_to_learn
from orderdesk.domain.catalogue import Customer, Product
from orderdesk.domain.draft import DraftLine, DraftOrder
from orderdesk.domain.extraction import ExtractedLine, ExtractedOrder, ProductInfo
from orderdesk.resolve.ladder import alias_words, resolve_line
from orderdesk.workflow.learn import make_learn_node
from orderdesk.workflow.state import WorkflowState
from tests.factories import make_email

PRODUCT = Product(code="PF60375", name="Tap", family="Tap", list_price=175.0)
OTHER = Product(code="PF60376", name="Black tap", family="Tap", finish="black", list_price=175.0)
CUSTOMER = Customer(id=2, name="Bains")


def _extracted_line(
    *, kind: str = "named", text: str | None = "robinet noir", code: str | None = None
) -> ExtractedLine:
    return ExtractedLine(
        raw_text="robinet noir",
        quantity=2,
        unit="piece",
        product=ProductInfo(kind=kind, text=text, code_as_written=code),
    )


def _approved(*, product: Product = PRODUCT, lines: list[int] | None = None) -> DraftOrder:
    line_indexes = [0] if lines is None else lines
    draft_lines = [
        DraftLine(line_index=index, raw_text="robinet noir", quantity=2, unit="piece",
                  product=product, unit_price=175.0, line_total=350.0)
        for index in line_indexes
    ]
    return DraftOrder(customer_id=CUSTOMER.id, po_number=None, lines=draft_lines, total=350.0)


def test_named_words_are_learned() -> None:
    extracted_line = _extracted_line()
    approved = _approved()

    assert aliases_to_learn(approved, ExtractedOrder(lines=[extracted_line])) == [
        (alias_words(extracted_line), "PF60375")
    ]


def test_previous_missing_and_code_only_lines_are_not_learned() -> None:
    extracted = ExtractedOrder(lines=[
        _extracted_line(kind="previous_order"),
        _extracted_line(kind="missing", text=None),
        _extracted_line(code="PF60375"),
    ])

    assert aliases_to_learn(_approved(lines=[0, 1, 2]), extracted) == []


def test_removed_line_is_not_learned() -> None:
    extracted = ExtractedOrder(lines=[_extracted_line(), _extracted_line(text="second tap")])

    assert aliases_to_learn(_approved(lines=[1]), extracted) == [("second tap", "PF60375")]


def test_corrected_line_learns_reviewers_product() -> None:
    catalogue = MemoryCatalogue([PRODUCT, OTHER], aliases=None)
    extracted = ExtractedOrder(lines=[_extracted_line()])
    approved = _approved(product=OTHER)
    state = WorkflowState(email=make_email(), approved_order=approved, extracted_order=extracted)

    update = make_learn_node(catalogue)(state)

    assert update["learned_aliases"] == 1
    assert catalogue.find_alias(CUSTOMER.id, "robinet noir") == "PF60376"


def test_conflict_forgets_alias_and_next_lookup_is_not_alias() -> None:
    catalogue = MemoryCatalogue([PRODUCT, OTHER], aliases={(CUSTOMER.id, "robinet noir"): "PF60375"})
    extracted = ExtractedOrder(lines=[_extracted_line()])
    approved = _approved(product=OTHER)
    state = WorkflowState(email=make_email(), approved_order=approved, extracted_order=extracted)

    update = make_learn_node(catalogue)(state)

    assert update["forgotten_aliases"] == 1
    assert catalogue.find_alias(CUSTOMER.id, "robinet noir") is None
    resolution = resolve_line(0, extracted.lines[0], CUSTOMER, catalogue, catalogue, catalogue)
    assert resolution.method != "alias"


def test_loop_closes_with_alias_lookup() -> None:
    catalogue = MemoryCatalogue([PRODUCT, OTHER])
    extracted = ExtractedOrder(lines=[_extracted_line()])
    approved = _approved(product=OTHER)

    make_learn_node(catalogue)(WorkflowState(email=make_email(), approved_order=approved, extracted_order=extracted))

    resolution = resolve_line(0, extracted.lines[0], CUSTOMER, catalogue, catalogue, catalogue)
    assert resolution.method == "alias"
    assert resolution.product == OTHER


def test_unknown_customer_learns_nothing() -> None:
    catalogue = MemoryCatalogue([PRODUCT])
    approved = DraftOrder(customer_id=None, po_number=None, lines=[DraftLine(
        line_index=0, raw_text="robinet noir", quantity=2, unit="piece", product=PRODUCT,
        unit_price=175.0, line_total=350.0,
    )], total=350.0)

    update = make_learn_node(catalogue)(WorkflowState(
        email=make_email(), approved_order=approved, extracted_order=ExtractedOrder(lines=[_extracted_line()])
    ))

    assert update == {"learned_aliases": 0, "forgotten_aliases": 0}
