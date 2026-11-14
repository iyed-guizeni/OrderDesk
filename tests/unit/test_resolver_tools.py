"""The Resolver agent's contract: its tools (read-only, customer-bound, small answers) and the code checks
on its final answer. The agent loop itself is tested separately."""

import datetime

from orderdesk.agents.resolver_checks import check_proposal
from orderdesk.agents.resolver_tools import MAX_PAST_ORDERS, make_resolver_tools
from orderdesk.catalogue.memory import MemoryCatalogue
from orderdesk.domain.catalogue import Candidate, Customer, PastOrder, PastOrderLine, Product
from orderdesk.domain.resolution import AgentProposal, LineResolution

ME = Customer(id=1, name="Bains & Co")
OTHER = Customer(id=2, name="Sanitaire Rhône")
CARTRIDGE = Product(code="CAR-35", name="Cartouche céramique 35 mm", family="Cartouche céramique 35 mm")
CHROME = Product(code="PH60251", name="Colonne thermostatique", family="Colonne thermostatique", finish="chrome")
BLACK = Product(code="PH60252", name="Colonne thermostatique", family="Colonne thermostatique", finish="noir mat")
OLD = Product(code="PF10000", name="Mitigeur ancien", family="Mitigeur ancien", finish="chrome", active=False)


def order(order_id: str, day: int, *products: Product) -> PastOrder:
    lines = [PastOrderLine(code=p.code, name=p.name, finish=p.finish, quantity=2) for p in products]
    return PastOrder(order_id=order_id, date=datetime.date(2026, 10, day), lines=lines)


ORDERS = {
    ME.id: [order(f"SO-{100 + day}", day, CARTRIDGE) for day in range(1, 8)] + [order("SO-200", 20, BLACK)],
    OTHER.id: [order("SO-999", 25, CHROME)],
}
CATALOGUE = MemoryCatalogue([CARTRIDGE, CHROME, BLACK, OLD], past_orders=ORDERS)


def tools_for(customer: Customer | None) -> dict:
    return {t.name: t for t in make_resolver_tools(CATALOGUE, CATALOGUE, customer)}


# ---------- the tools ----------


def test_no_tool_lets_the_model_choose_the_customer() -> None:
    for name, tool in tools_for(ME).items():
        assert not {"customer", "customer_id", "tenant_id"} & set(tool.args), name


def test_without_a_known_customer_there_is_no_history_tool() -> None:
    assert sorted(tools_for(None)) == ["get_product", "search_catalogue"]


def test_past_orders_are_this_customers_newest_first() -> None:
    rows = tools_for(ME)["find_past_orders"].invoke({"limit": 2}).splitlines()

    assert rows == [
        "SO-200 | 2026-10-20 | PH60252 | Colonne thermostatique | noir mat | qty 2",
        "SO-107 | 2026-10-07 | CAR-35 | Cartouche céramique 35 mm | no finish | qty 2",
    ]


def test_another_customers_orders_never_appear() -> None:
    assert "SO-999" not in tools_for(ME)["find_past_orders"].invoke({"limit": MAX_PAST_ORDERS})


def test_a_huge_limit_is_clamped() -> None:
    rows = tools_for(ME)["find_past_orders"].invoke({"limit": 500}).splitlines()

    assert len(rows) == MAX_PAST_ORDERS


def test_get_product_shows_unit_and_status() -> None:
    assert tools_for(ME)["get_product"].invoke({"code": "pf 10000"}) == (
        "PF10000 | Mitigeur ancien | chrome | sold by piece | discontinued"
    )


def test_problems_come_back_as_guidance_not_errors() -> None:
    tools = tools_for(ME)

    assert tools["get_product"].invoke({"code": "XX1"}).startswith("No product with code 'XX1'.")
    assert tools["search_catalogue"].invoke({"text": "receveur"}).startswith("No product matches 'receveur'.")


def test_search_returns_one_product_per_line() -> None:
    rows = tools_for(ME)["search_catalogue"].invoke({"text": "colonne thermostatique"}).splitlines()

    assert sorted(rows) == [
        "PH60251 | Colonne thermostatique | chrome | sold by piece | active",
        "PH60252 | Colonne thermostatique | noir mat | sold by piece | active",
    ]


# ---------- the checks on the final answer ----------


def unresolved(reason: str, *candidates: Product) -> LineResolution:
    return LineResolution(line_index=0, status="unresolved", reason=reason, next_step="agent",
                          candidates=[Candidate(product=p, score=1.0) for p in candidates])


def proposal(code: str, source: str = "catalogue", order_id: str | None = None) -> AgentProposal:
    return AgentProposal(evidence="(evidence)", source=source, source_order_id=order_id, code=code)


def test_a_proposal_backed_by_a_real_past_order_is_accepted() -> None:
    product, problem = check_proposal(proposal("CAR-35", "past_order", "SO-101"), unresolved("previous_order"),
                                      CATALOGUE, ME)

    assert (product, problem) == (CARTRIDGE, None)


def test_none_is_rejected() -> None:
    assert check_proposal(proposal("NONE", "none"), unresolved("no_candidates"), CATALOGUE, ME)[0] is None


def test_an_invented_code_is_rejected() -> None:
    _, problem = check_proposal(proposal("CAR-99"), unresolved("no_candidates"), CATALOGUE, ME)

    assert problem == "code 'CAR-99' does not exist"


def test_an_order_of_another_customer_is_rejected() -> None:
    _, problem = check_proposal(proposal("PH60251", "past_order", "SO-999"), unresolved("previous_order"),
                                CATALOGUE, ME)

    assert problem == "order 'SO-999' is not a confirmed order of this customer"


def test_an_order_that_does_not_contain_the_product_is_rejected() -> None:
    _, problem = check_proposal(proposal("PH60251", "past_order", "SO-101"), unresolved("previous_order"),
                                CATALOGUE, ME)

    assert problem == "order SO-101 does not contain PH60251"


def test_same_as_last_time_must_come_from_a_past_order() -> None:
    _, problem = check_proposal(proposal("CAR-35"), unresolved("previous_order"), CATALOGUE, ME)

    assert problem == "'same as last time' must be answered from a past order"


def test_a_missing_finish_must_be_one_of_the_searched_finishes() -> None:
    _, problem = check_proposal(proposal("CAR-35"), unresolved("variant_missing", CHROME, BLACK), CATALOGUE, ME)

    assert problem == "CAR-35 is not one of the finishes found by the search"


def test_a_usual_finish_from_history_is_accepted() -> None:
    product, _ = check_proposal(proposal("PH60252", "past_order", "SO-200"),
                                unresolved("variant_missing", CHROME, BLACK), CATALOGUE, ME)

    assert product == BLACK
