import datetime

import pytest

from orderdesk.catalogue.memory import MemoryCatalogue
from orderdesk.checks.order import build_draft
from orderdesk.domain.catalogue import Customer, PastOrder, PastOrderLine, Product
from orderdesk.domain.extraction import ExtractedLine, ExtractedOrder, ProductInfo
from orderdesk.domain.resolution import LineResolution, ResolvedOrder


def _line(
    raw_text: str = "2 pieces",
    quantity: float | None = 2,
    unit: str | None = "piece",
    kind: str = "named",
    text: str | None = None,
) -> ExtractedLine:
    return ExtractedLine(
        raw_text=raw_text,
        quantity=quantity,
        unit=unit,
        product=ProductInfo(kind=kind, text=text),
    )


def _resolved(line_index: int, product: Product | None, customer: Customer | None = None) -> ResolvedOrder:
    if product is None:
        resolution = LineResolution(
            line_index=line_index,
            status="unresolved",
            reason="no_candidates",
            next_step="human",
        )
    else:
        resolution = LineResolution(
            line_index=line_index,
            status="resolved",
            product=product,
            method="exact_code",
            next_step="done",
        )
    return ResolvedOrder(customer=customer, lines=[resolution])


def test_resolved_line_gets_price_and_totals() -> None:
    product = Product(code="P1", name="Product", family="Product", list_price=12.5)
    extracted = ExtractedOrder(lines=[_line(quantity=3)])

    draft = build_draft(extracted, _resolved(0, product), MemoryCatalogue([product]))

    assert draft.lines[0].unit_price == 12.5
    assert draft.lines[0].line_total == 37.5
    assert draft.total == 37.5


def test_unresolved_line_has_no_product_and_no_total() -> None:
    extracted = ExtractedOrder(lines=[_line()])
    draft = build_draft(extracted, _resolved(0, None), MemoryCatalogue([]))

    assert draft.lines[0].issues == ["no_product"]
    assert draft.lines[0].unit_price is None
    assert draft.lines[0].line_total is None
    assert draft.total == 0.0


def test_no_quantity_issue() -> None:
    product = Product(code="P1", name="Product", family="Product", list_price=10)
    draft = build_draft(
        ExtractedOrder(lines=[_line(quantity=None)]),
        _resolved(0, product),
        MemoryCatalogue([product]),
    )
    assert draft.lines[0].issues == ["no_quantity"]


def test_no_price_issue() -> None:
    product = Product(code="P1", name="Product", family="Product")
    draft = build_draft(ExtractedOrder(lines=[_line()]), _resolved(0, product), MemoryCatalogue([product]))
    assert draft.lines[0].issues == ["no_price"]


def test_discontinued_issue() -> None:
    product = Product(code="P1", name="Product", family="Product", list_price=10, active=False)
    draft = build_draft(ExtractedOrder(lines=[_line()]), _resolved(0, product), MemoryCatalogue([product]))
    assert draft.lines[0].issues == ["discontinued"]


def test_unit_differs_rules() -> None:
    product = Product(code="P1", name="Product", family="Product", uom="piece", list_price=10)
    box = build_draft(ExtractedOrder(lines=[_line(unit="box")]), _resolved(0, product), MemoryCatalogue([product]))
    none = build_draft(ExtractedOrder(lines=[_line(unit=None)]), _resolved(0, product), MemoryCatalogue([product]))
    assert "unit_differs" in box.lines[0].issues
    assert "unit_differs" not in none.lines[0].issues

    meter = Product(code="M1", name="Meter", family="Meter", uom="meter", list_price=8)
    meter_draft = build_draft(
        ExtractedOrder(lines=[_line(unit="meter")]), _resolved(0, meter), MemoryCatalogue([meter])
    )
    assert "unit_differs" not in meter_draft.lines[0].issues


def test_po_already_used_only_for_matching_customer() -> None:
    customer = Customer(id=2, name="Bains")
    order = PastOrder(
        order_id="SO-2291",
        date=datetime.date(2026, 10, 2),
        client_ref="BC-2187",
        lines=[PastOrderLine(code="P1", name="Product", quantity=1)],
    )
    catalogue = MemoryCatalogue([], past_orders={2: [order]})
    extracted = ExtractedOrder(po_number="bc 2187", lines=[])

    draft = build_draft(extracted, ResolvedOrder(customer=customer, lines=[]), catalogue)

    assert draft.order_issues[0].kind == "po_already_used"
    assert "SO-2291" in draft.order_issues[0].detail


def test_unknown_customer_and_no_lines() -> None:
    customer_draft = build_draft(ExtractedOrder(lines=[]), ResolvedOrder(customer=None, lines=[]), MemoryCatalogue([]))
    assert [issue.kind for issue in customer_draft.order_issues] == ["unknown_customer", "no_lines"]


def test_a_po_of_another_customer_is_not_a_duplicate() -> None:
    order = PastOrder(order_id="SO-2291", date=datetime.date(2026, 10, 2), client_ref="BC-2187",
                      lines=[PastOrderLine(code="P1", name="Product", quantity=1)])
    catalogue = MemoryCatalogue([], past_orders={2: [order]})

    draft = build_draft(ExtractedOrder(po_number="BC-2187", lines=[_line()]),
                        _resolved(0, None, Customer(id=1, name="Other")), catalogue)

    assert draft.order_issues == []


def test_an_unknown_customer_skips_the_po_check() -> None:
    class NoPoLookup(MemoryCatalogue):
        def find_order_by_client_ref(self, customer_id, client_ref):
            raise AssertionError("no customer: there is nobody to look the PO up for")

    draft = build_draft(ExtractedOrder(po_number="4471", lines=[_line()]), _resolved(0, None), NoPoLookup([]))

    assert [issue.kind for issue in draft.order_issues] == ["unknown_customer"]


def test_the_draft_holds_no_customer_name() -> None:
    product = Product(code="P1", name="Product", family="Product", list_price=10)
    customer = Customer(id=2, name="Bains & Co")

    draft = build_draft(ExtractedOrder(lines=[_line()]), _resolved(0, product, customer), MemoryCatalogue([product]))

    assert draft.customer_id == 2
    assert "Bains & Co" not in draft.model_dump_json()


def test_the_node_refuses_to_run_before_resolve() -> None:
    from orderdesk.workflow.state import WorkflowState
    from orderdesk.workflow.validate import make_validate_node
    from tests.factories import make_email

    with pytest.raises(ValueError, match="extracted and resolved"):
        make_validate_node(MemoryCatalogue([]))(WorkflowState(email=make_email()))


def test_prices_never_reach_the_llm() -> None:
    """Only Validate reads list_price: not the agent's tools, not the choice prompt."""
    from orderdesk.agents.resolver_tools import make_resolver_tools
    from orderdesk.domain.catalogue import Candidate
    from orderdesk.prompts.choose_candidate import build_user_prompt
    from tests.factories import make_email

    product = Product(code="P1", name="Mitigeur", family="Mitigeur", list_price=987.65)
    order = PastOrder(order_id="SO-1", date=datetime.date(2026, 10, 2),
                      lines=[PastOrderLine(code="P1", name="Mitigeur", quantity=1)])
    catalogue = MemoryCatalogue([product], past_orders={2: [order]})
    tools = {tool.name: tool for tool in make_resolver_tools(catalogue, catalogue, Customer(id=2, name="Bains"))}

    seen = [
        tools["search_catalogue"].invoke({"text": "mitigeur"}),
        tools["get_product"].invoke({"code": "P1"}),
        tools["find_past_orders"].invoke({"limit": 3}),
        build_user_prompt(make_email(), _line(text="mitigeur"), [Candidate(product=product, score=1.0)]),
    ]

    assert all("987" not in text for text in seen), seen
