"""The deterministic resolve ladder: alias -> exact code -> search, and where unresolved lines go next."""

import pytest
from pydantic import ValidationError

from orderdesk.catalogue.memory import MemoryCatalogue
from orderdesk.domain.catalogue import Customer, Product
from orderdesk.domain.extraction import ExtractedLine, ExtractedOrder, ProductInfo
from orderdesk.domain.resolution import LineResolution
from orderdesk.resolve.ladder import resolve_line, resolve_order
from orderdesk.workflow.resolve import make_resolve_node
from orderdesk.workflow.state import WorkflowState
from tests.factories import make_email

KNOWN = Customer(id=1, name="Bains & Co")
KNOWN_EMAIL = "contact@bains-and-co.example"


def mixer(code: str, finish: str) -> Product:
    return Product(code=code, name="Mitigeur lavabo Profilo", family="Mitigeur lavabo Profilo", finish=finish)


PRODUCTS = [
    mixer("PF60375", "chrome"),
    mixer("PF60376", "noir brossé"),
    Product(code="PF60380", name="Mitigeur bain-douche Profilo", family="Mitigeur bain-douche Profilo",
            finish="chrome"),
    Product(code="PH60251", name="Colonne de douche thermostatique", family="Colonne de douche thermostatique",
            finish="chrome"),
    Product(code="CAR-35", name="Cartouche céramique 35 mm", family="Cartouche céramique 35 mm"),
]


def make_catalogue(aliases: dict | None = None) -> MemoryCatalogue:
    return MemoryCatalogue(PRODUCTS, {KNOWN_EMAIL: KNOWN}, aliases)


def make_line(kind: str = "named", text: str | None = None, variant: str | None = None,
              code: str | None = None) -> ExtractedLine:
    product = ProductInfo(kind=kind, text=text, variant_text=variant, code_as_written=code)
    return ExtractedLine(raw_text="(as written)", quantity=2, product=product)


def resolve(line: ExtractedLine, customer: Customer | None = KNOWN, catalogue: MemoryCatalogue | None = None):
    catalogue = catalogue or make_catalogue()
    return resolve_line(0, line, customer, catalogue, catalogue, catalogue)


# ---------- rung 1: alias ----------


def test_approved_alias_resolves_first() -> None:
    catalogue = make_catalogue({(KNOWN.id, "robinet profilo noir"): "PF60376"})

    result = resolve(make_line(text="robinet Profilo", variant="noir"), catalogue=catalogue)

    assert (result.status, result.method, result.product.code) == ("resolved", "alias", "PF60376")


def test_alias_needs_a_known_customer() -> None:
    catalogue = make_catalogue({(KNOWN.id, "robinet profilo noir"): "PF60376"})

    result = resolve(make_line(text="robinet Profilo", variant="noir"), customer=None, catalogue=catalogue)

    assert result.method != "alias"


def test_alias_to_a_deleted_product_is_ignored() -> None:
    catalogue = make_catalogue({(KNOWN.id, "cartouche 35"): "OLD-123"})

    result = resolve(make_line(text="cartouche", variant="35"), catalogue=catalogue)

    assert (result.method, result.product.code) == ("search", "CAR-35")


# ---------- rung 2: exact code ----------


def test_written_code_resolves_ignoring_case_and_spaces() -> None:
    result = resolve(make_line(code="pf 60375"))

    assert (result.status, result.method, result.product.code) == ("resolved", "exact_code", "PF60375")


def test_unknown_code_falls_back_to_the_words() -> None:
    result = resolve(make_line(code="XX999", text="colonne de douche thermostatique", variant="chrome"))

    assert (result.method, result.product.code) == ("search", "PH60251")


# ---------- rung 3: search ----------


def test_clear_search_winner_resolves() -> None:
    result = resolve(make_line(text="mitigeurs lavabo Profilo", variant="noir brossé"))

    assert (result.status, result.method, result.product.code) == ("resolved", "search", "PF60376")


def test_same_product_in_several_finishes_is_variant_missing_for_the_agent() -> None:
    result = resolve(make_line(text="mitigeur lavabo Profilo"))

    assert (result.status, result.reason, result.next_step) == ("unresolved", "variant_missing", "agent")
    assert {c.product.code for c in result.candidates} >= {"PF60375", "PF60376"}


def test_variant_missing_goes_to_a_person_when_the_customer_is_unknown() -> None:
    result = resolve(make_line(text="mitigeur lavabo Profilo"), customer=None)

    assert (result.reason, result.next_step) == ("variant_missing", "human")


def test_different_products_matching_equally_are_ambiguous_for_a_person() -> None:
    result = resolve(make_line(text="mitigeur Profilo", variant="chrome"))  # basin or bath-shower?

    assert (result.status, result.reason, result.next_step) == ("unresolved", "ambiguous", "human")


def test_nothing_close_in_the_catalogue_goes_to_the_agent() -> None:
    result = resolve(make_line(text="receveur extra-plat"))

    assert (result.status, result.reason, result.next_step) == ("unresolved", "no_candidates", "agent")


# ---------- lines the ladder does not try ----------


def test_line_without_product_goes_straight_to_a_person() -> None:
    result = resolve(make_line(kind="missing"))

    assert (result.reason, result.next_step) == ("product_missing", "human")


def test_previous_order_line_goes_to_the_agent_without_searching() -> None:
    result = resolve(make_line(kind="previous_order", text="cartouches"))

    assert (result.reason, result.next_step, result.candidates) == ("previous_order", "agent", [])


def test_previous_order_goes_to_a_person_when_the_customer_is_unknown() -> None:
    result = resolve(make_line(kind="previous_order", text="cartouches"), customer=None)

    assert result.next_step == "human"


# ---------- the whole order ----------


def test_order_finds_the_customer_from_the_sender_and_keeps_line_order() -> None:
    catalogue = make_catalogue()
    order = ExtractedOrder(lines=[make_line(code="PF60375"), make_line(kind="missing")])

    result = resolve_order(order, KNOWN_EMAIL.upper(), catalogue, catalogue, catalogue)

    assert result.customer == KNOWN
    assert [(line.line_index, line.status) for line in result.lines] == [(0, "resolved"), (1, "unresolved")]


def test_resolved_line_without_product_is_rejected() -> None:
    with pytest.raises(ValidationError, match="needs a product"):
        LineResolution(line_index=0, status="resolved", method="search", next_step="done")


# ---------- the node ----------


def test_node_returns_only_the_resolved_order() -> None:
    catalogue = make_catalogue()
    state = WorkflowState(email=make_email(sender=KNOWN_EMAIL),
                          extracted_order=ExtractedOrder(lines=[make_line(code="PF60375")]))

    update = make_resolve_node(catalogue, catalogue, catalogue)(state)

    assert list(update) == ["resolved_order"]
    assert update["resolved_order"].lines[0].product.code == "PF60375"


def test_node_refuses_to_run_before_extraction() -> None:
    catalogue = make_catalogue()

    with pytest.raises(ValueError, match="before extraction"):
        make_resolve_node(catalogue, catalogue, catalogue)(WorkflowState(email=make_email()))
