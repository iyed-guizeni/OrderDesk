"""The Resolver eval metrics and its case file: no LLM, no LangSmith."""

import pytest

from evals.resolver_metrics import outcome, run_facts, score_case, summarize
from evals.run_resolver import CATALOGUE, CUSTOMERS, REPEAT_CASES, FaultyCatalogue, load_cases, starting_point
from orderdesk.resolve.ladder import next_step, resolve_line
from tests.unit.test_resolver_agent import SAME_AS_LAST_TIME, answer, call, run_agent

INPUTS = {"line": {"raw_text": SAME_AS_LAST_TIME.raw_text}, "customer_id": 2}
EXPECTED = {"code": "CAR-35", "required_tools": ["find_past_orders"], "max_model_calls": 4}


def scores_of(*answers, expected=EXPECTED) -> dict:
    return score_case(INPUTS, run_facts(run_agent(*answers), 1.0), expected, CATALOGUE)


# ---------- outcome ----------


def test_the_right_product_with_the_right_tools() -> None:
    scores = scores_of(call("find_past_orders"), answer("CAR-35"))

    assert scores["correct"] and scores["required_tools"] and scores["within_budget"]
    assert (scores["silent_wrong"], scores["hallucinated"], scores["model_calls"]) == (False, False, 2)


def test_a_product_where_none_was_expected_is_silent_wrong() -> None:
    scores = scores_of(call("find_past_orders"), answer("CAR-35"), expected={**EXPECTED, "code": None})

    assert scores["silent_wrong"] and not scores["correct"]


def test_an_existing_but_wrong_product_is_silent_wrong_not_a_hallucination() -> None:
    scores = scores_of(answer("CAR-40", source="catalogue", order_id=None))

    assert scores["silent_wrong"] and not scores["hallucinated"]


def test_an_honest_none_is_a_correct_handover() -> None:
    scores = scores_of(call("find_past_orders"), answer("NONE", "none", None), expected={**EXPECTED, "code": None})

    assert scores["correct"]


def test_an_invented_code_is_a_hallucination() -> None:
    scores = scores_of(answer("CAR-99"))

    assert scores["hallucinated"] and scores["silent_wrong"]
    assert not scores["required_tools"]  # it answered without looking


def test_an_invented_order_is_a_hallucination() -> None:
    assert scores_of(answer("CAR-35", order_id="SO-9999"))["hallucinated"]


def test_the_four_outcomes() -> None:
    assert outcome({"code": "CAR-35"}, "CAR-35") == "correct_product"
    assert outcome({"code": "CAR-35", "accept_human": True}, None) == "correct_human"
    assert outcome({"code": "CAR-35"}, None) == "missed"
    assert outcome({"code": None}, "CAR-35") == "silent_wrong"


# ---------- behaviour ----------


def test_errors_repeats_and_invented_lookups_are_counted() -> None:
    scores = scores_of(call("get_product", code="CAR-77"), call("get_product", code="CAR-77"),
                       call("AgentProposal", code="CAR-35"), call("find_past_orders"), answer("CAR-35"))

    # the 2nd lookup is a repeat, not invented: the 1st reply quoted the code back
    assert (scores["invented_lookups"], scores["repeated_calls"], scores["model_errors"]) == (1, 1, 1)
    assert not scores["within_budget"]  # 5 calls for a budget of 4


def test_a_code_invented_inside_a_search_is_counted() -> None:
    """nc-08: the client wrote PF6037, the agent looked up PF60375 (it picked the missing digit itself)."""
    assert scores_of(call("search_catalogue", text="mitigeur PF60375"), answer("CAR-35"))["invented_lookups"] == 1


def test_codes_read_in_the_line_or_a_tool_result_are_not_invented() -> None:
    scores = scores_of(call("find_past_orders"), call("get_product", code="CAR-35"),
                       call("search_catalogue", text="cartouche 35mm"), answer("CAR-35"))

    assert scores["invented_lookups"] == 0  # CAR-35 came from find_past_orders; 35mm is not a code


# ---------- the experiment ----------


GOOD = {"correct": True, "silent_wrong": False, "providers_failed": False, "invented_lookups": 0, "model_calls": 2,
        "seconds": 1.0, "tokens": 100}


def test_the_verdict() -> None:
    assert summarize([GOOD] * 9 + [{**GOOD, "correct": False}])["verdict"] == "PASS"
    assert summarize([GOOD] * 9 + [{**GOOD, "correct": False, "silent_wrong": True}])["verdict"] == "FAIL"
    assert summarize([GOOD] * 7 + [{**GOOD, "correct": False}] * 3)["verdict"] == "FAIL"  # 70% < 80%
    assert summarize([GOOD] * 9 + [{**GOOD, "providers_failed": True}])["verdict"] == "RERUN"


# ---------- the case file and the faults ----------


@pytest.mark.parametrize("case", load_cases(), ids=lambda case: case["id"])
def test_every_case_is_what_production_would_send_to_the_agent(case) -> None:
    """The eval reads the cases as written; this checks them against the real ladder, so they cannot drift."""
    inputs = case["input"]
    line, resolution = starting_point(inputs)
    customer = CUSTOMERS.get(inputs["customer_id"])
    ladder = resolve_line(0, line, customer, CATALOGUE, CATALOGUE, CATALOGUE)

    # llm_said_none: the ladder found the line ambiguous, then the choice step said no candidate fits
    assert ladder.reason == ("ambiguous" if inputs["reason"] == "llm_said_none" else inputs["reason"])
    assert [c.product.code for c in ladder.candidates] == inputs.get("candidates", [])
    assert next_step(inputs["reason"], customer is not None) == "agent"
    code = case["expected"]["code"]
    assert code is None or CATALOGUE.get_product(code) is not None
    assert resolution.reason == inputs["reason"]


def test_a_fault_breaks_one_tool_once() -> None:
    faulty = FaultyCatalogue(CATALOGUE, {"tool": "find_past_orders", "kind": "error_once"})

    with pytest.raises(ConnectionError):
        faulty.past_orders(2, 3)
    assert faulty.past_orders(2, 3)  # works the second time
    assert faulty.get_product("CAR-35")  # the other methods are untouched


def test_the_repeated_cases_exist() -> None:
    assert set(REPEAT_CASES) <= {case["id"] for case in load_cases()}
