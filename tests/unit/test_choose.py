"""LLM choice among search candidates: only ambiguous lines, only listed codes, NONE goes on to the agent."""

import pytest
from langgraph.graph import END

from orderdesk.catalogue.memory import MemoryCatalogue
from orderdesk.domain.catalogue import Customer, Product
from orderdesk.domain.extraction import ExtractedLine, ExtractedOrder, ProductInfo
from orderdesk.domain.resolution import CandidateChoice
from orderdesk.prompts.choose_candidate import build_user_prompt
from orderdesk.resolve.ladder import resolve_order
from orderdesk.workflow.choose import make_choose_node, needs_choice
from orderdesk.workflow.state import WorkflowState
from tests.factories import make_email
from tests.fakes import FakeLLM

PROFILE = "ACME Robinetterie sells taps and shower systems."
KNOWN_EMAIL = "contact@bains-and-co.example"
PRODUCTS = [
    Product(code="PF60375", name="Mitigeur lavabo Profilo", family="Mitigeur lavabo Profilo", finish="chrome"),
    Product(code="PF60380", name="Mitigeur bain-douche Profilo", family="Mitigeur bain-douche Profilo",
            finish="chrome"),
]
CATALOGUE = MemoryCatalogue(PRODUCTS, {KNOWN_EMAIL: Customer(id=1, name="Bains & Co")})
BODY = "Bonjour,\n2 mitigeurs Profilo chrome pour la salle de bain du chantier.\nMerci"


def ambiguous_line() -> ExtractedLine:
    return ExtractedLine(raw_text="2 mitigeurs Profilo chrome", quantity=2,
                         product=ProductInfo(kind="named", text="mitigeurs Profilo", variant_text="chrome"))


def code_line() -> ExtractedLine:
    return ExtractedLine(raw_text="PF60375 x 1", quantity=1,
                         product=ProductInfo(kind="named", code_as_written="PF60375"))


def make_state(*lines: ExtractedLine, sender: str = KNOWN_EMAIL) -> WorkflowState:
    order = ExtractedOrder(lines=list(lines))
    return WorkflowState(email=make_email(sender=sender, body=BODY), extracted_order=order,
                         resolved_order=resolve_order(order, sender, CATALOGUE, CATALOGUE, CATALOGUE))


def choice(code: str) -> CandidateChoice:
    return CandidateChoice(evidence="pour la salle de bain", code=code)


def run(llm: FakeLLM, state: WorkflowState):
    return make_choose_node(llm, PROFILE)(state)["resolved_order"]


# ---------- routing ----------


def test_resolve_sends_ambiguous_orders_to_choose() -> None:
    assert needs_choice(make_state(ambiguous_line())) == "choose"


def test_resolve_skips_choose_when_nothing_is_ambiguous() -> None:
    assert needs_choice(make_state(code_line())) == END


# ---------- the answer ----------


def test_listed_code_resolves_the_line_with_its_evidence() -> None:
    line = run(FakeLLM(choice("PF60380")), make_state(ambiguous_line())).lines[0]

    assert (line.status, line.method, line.product.code) == ("resolved", "llm_choice", "PF60380")
    assert line.evidence == "pour la salle de bain"


def test_none_goes_to_the_agent_when_the_customer_is_known() -> None:
    line = run(FakeLLM(choice("NONE")), make_state(ambiguous_line())).lines[0]

    assert (line.status, line.reason, line.next_step) == ("unresolved", "llm_said_none", "agent")


def test_none_goes_to_a_person_when_the_customer_is_unknown() -> None:
    line = run(FakeLLM(choice("NONE")), make_state(ambiguous_line(), sender="someone@unknown.example")).lines[0]

    assert line.next_step == "human"


def test_a_code_that_is_not_a_candidate_is_never_trusted() -> None:
    line = run(FakeLLM(choice("PH60251")), make_state(ambiguous_line())).lines[0]

    assert (line.status, line.reason) == ("unresolved", "llm_said_none")


def test_only_ambiguous_lines_reach_the_llm_and_others_are_kept() -> None:
    llm = FakeLLM(choice("PF60380"))

    order = run(llm, make_state(code_line(), ambiguous_line()))

    assert len(llm.calls) == 1
    assert [(line.method, line.product.code) for line in order.lines] == [("exact_code", "PF60375"),
                                                                          ("llm_choice", "PF60380")]


def test_no_valid_answer_fails_clearly() -> None:
    with pytest.raises(ValueError, match="no valid CandidateChoice"):
        run(FakeLLM(None), make_state(ambiguous_line()))


def test_provider_errors_are_not_hidden() -> None:
    with pytest.raises(TimeoutError):
        run(FakeLLM(TimeoutError("provider too slow")), make_state(ambiguous_line()))


# ---------- the prompts ----------


def test_user_prompt_lists_each_candidate_on_its_own_line_without_scores() -> None:
    state = make_state(ambiguous_line())
    resolution = state.resolved_order.lines[0]

    prompt = build_user_prompt(state.email, ambiguous_line(), resolution.candidates)

    candidate_lines = prompt.split("<candidates>\n")[1].split("\n</candidates>")[0].splitlines()
    assert sorted(candidate_lines) == ["PF60375 | Mitigeur lavabo Profilo | chrome",
                                       "PF60380 | Mitigeur bain-douche Profilo | chrome"]


def test_user_prompt_gives_the_line_and_the_email_but_not_the_sender() -> None:
    llm = FakeLLM(choice("PF60380"))

    run(llm, make_state(ambiguous_line()))

    lines = llm.last_messages[1].content.splitlines()
    assert "Written: 2 mitigeurs Profilo chrome" in lines
    assert "Variant: chrome" in lines
    assert "2 mitigeurs Profilo chrome pour la salle de bain du chantier." in lines
    assert KNOWN_EMAIL not in llm.last_messages[1].content


def test_system_prompt_contains_the_profile_inside_business_tags() -> None:
    llm = FakeLLM(choice("PF60380"))

    run(llm, make_state(ambiguous_line()))

    system = llm.last_messages[0].content.splitlines()
    assert system[system.index("<business>") + 1] == PROFILE
