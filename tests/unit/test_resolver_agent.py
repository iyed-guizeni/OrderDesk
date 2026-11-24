"""The Resolver agent loop and the agent step: scripted model answers, the real tools and checks, no network."""

import itertools

from langchain_core.messages import AIMessage, ToolMessage

from orderdesk.agents.resolver_agent import MAX_MODEL_CALLS, TOOL_FAILED, AgentRun, run_resolver_agent
from orderdesk.agents.resolver_tools import make_resolver_tools
from orderdesk.catalogue.memory import MemoryCatalogue
from orderdesk.domain.catalogue import Customer
from orderdesk.domain.classification import EmailClassification
from orderdesk.domain.extraction import ExtractedLine, ExtractedOrder, ProductInfo
from orderdesk.domain.resolution import LineResolution
from orderdesk.llm.client import AllProvidersFailed
from orderdesk.profiles.demo_catalogue import DEMO_PAST_ORDERS, DEMO_PRODUCTS
from orderdesk.workflow.graph import build_graph
from orderdesk.workflow.resolve_agent import agent_line
from tests.factories import make_email
from tests.fakes import FakeLLM, FakeStorage

BAINS = Customer(id=2, name="Bains & Co")  # in the demo history: SO-2291 has CAR-35
CATALOGUE = MemoryCatalogue(DEMO_PRODUCTS, {"contact@bains-and-co.example": BAINS}, past_orders=DEMO_PAST_ORDERS)
SAME_AS_LAST_TIME = ExtractedLine(
    raw_text="4 cartouches comme la dernière fois", quantity=4,
    product=ProductInfo(kind="previous_order", text="cartouches", reference_text="comme la dernière fois"),
)
_ids = itertools.count()


def call(name: str, **args) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call-{next(_ids)}"}])


def answer(code: str, source: str = "past_order", order_id: str | None = "SO-2291") -> AIMessage:
    return call("AgentProposal", evidence="SO-2291 | 2026-10-02 | CAR-35 | qty 4", source=source,
                source_order_id=order_id, code=code)


class FakeAgentLLM:
    """A ToolBindable: records which tools were bound, then answers with the scripted messages."""

    def __init__(self, *answers) -> None:
        self.llm = FakeLLM(*answers)
        self.bound: list[str] = []

    def bind_tools(self, tools) -> "FakeAgentLLM":
        self.bound = [getattr(t, "name", getattr(t, "__name__", "?")) for t in tools]
        return self

    def invoke(self, messages):
        answer = self.llm.invoke(list(messages))
        return answer.model_copy() if isinstance(answer, AIMessage) else answer  # a real model: a new message


def previous_order_line() -> LineResolution:
    return LineResolution(line_index=0, status="unresolved", reason="previous_order", next_step="agent")


def run(agent: FakeAgentLLM, customer: Customer | None = BAINS) -> LineResolution:
    return agent_line(agent, "system", SAME_AS_LAST_TIME, previous_order_line(), customer, CATALOGUE, CATALOGUE)


def test_same_as_last_time_is_resolved_from_a_real_past_order() -> None:
    agent = FakeAgentLLM(call("find_past_orders", limit=3), answer("CAR-35"))

    result = run(agent)

    assert (result.status, result.product.code, result.method) == ("resolved", "CAR-35", "agent")
    assert "SO-2291" in result.evidence  # shown to the reviewer
    tool_reply = agent.llm.calls[1][-1]
    assert isinstance(tool_reply, ToolMessage) and "SO-2291 | 2026-10-02 | CAR-35" in tool_reply.content


def test_the_final_answer_is_bound_as_one_more_tool() -> None:
    agent = FakeAgentLLM(answer("CAR-35"))
    run(agent)

    assert sorted(agent.bound) == ["AgentProposal", "find_past_orders", "get_product", "search_catalogue"]


def test_an_invented_code_goes_to_a_person() -> None:
    result = run(FakeAgentLLM(answer("CAR-99")))

    assert (result.status, result.reason, result.next_step) == ("unresolved", "agent_failed", "human")
    assert result.evidence == "proposal rejected: code 'CAR-99' does not exist"


def test_an_invalid_proposal_can_be_fixed_once() -> None:
    agent = FakeAgentLLM(call("AgentProposal", code="CAR-35"), answer("CAR-35"))  # 1st: evidence missing

    result = run(agent)

    assert result.status == "resolved"
    reply = agent.llm.calls[1][-1]
    assert (reply.name, reply.status) == ("AgentProposal", "error")  # ToolNode sent the error back


def test_a_text_answer_without_proposal_goes_to_a_person() -> None:
    result = run(FakeAgentLLM(AIMessage(content="I think it is CAR-35.")))

    assert (result.reason, result.evidence) == ("agent_failed", "the agent stopped without calling AgentProposal")


def test_the_loop_always_ends() -> None:
    agent = FakeAgentLLM(call("search_catalogue", text="cartouche"))  # searches forever

    result = run(agent)

    assert result.evidence == f"no answer after {MAX_MODEL_CALLS} model calls"
    assert len(agent.llm.calls) == MAX_MODEL_CALLS


def test_an_unknown_tool_is_answered_with_guidance() -> None:
    agent = FakeAgentLLM(call("update_order", code="CAR-35"), answer("CAR-35"))

    run(agent)

    assert "update_order is not a valid tool" in agent.llm.calls[1][-1].content


def test_providers_down_sends_the_line_to_a_person_without_crashing() -> None:
    result = run(FakeAgentLLM(AllProvidersFailed(["nvidia: 503"])))

    assert (result.reason, result.next_step) == ("agent_failed", "human")


def test_the_prompt_carries_no_sender_or_customer() -> None:
    agent = FakeAgentLLM(answer("CAR-35"))
    agent_line(agent, "system", SAME_AS_LAST_TIME, previous_order_line(), BAINS, CATALOGUE, CATALOGUE)

    prompt = agent.llm.calls[0][1].content
    assert "bains" not in prompt.lower() and "@" not in prompt


# ---------- in the workflow ----------


def test_the_graph_sends_agent_lines_to_the_agent() -> None:
    classify = FakeLLM(EmailClassification(evidence="commande", label="new_order", confidence="high"))
    extract = FakeLLM(ExtractedOrder(lines=[SAME_AS_LAST_TIME]))
    agent = FakeAgentLLM(call("find_past_orders"), answer("CAR-35"))
    graph = build_graph(classify, extract, FakeLLM(None), agent, "profile", FakeStorage(), CATALOGUE, CATALOGUE,
                        CATALOGUE)

    result = graph.invoke({"email": make_email(sender="contact@bains-and-co.example")})

    line = result["resolved_order"].lines[0]
    assert (line.product.code, line.method) == ("CAR-35", "agent")


def test_an_unknown_sender_gets_no_history_tool() -> None:
    agent = FakeAgentLLM(answer("NONE", "none", None))
    line = LineResolution(line_index=0, status="unresolved", reason="no_candidates", next_step="agent")

    result = agent_line(agent, "system", SAME_AS_LAST_TIME, line, None, CATALOGUE, CATALOGUE)

    assert "find_past_orders" not in agent.bound
    assert result.evidence == "proposal rejected: the agent found nothing"



# ---------- the loop alone: what the evals read ----------


def run_agent(*answers) -> AgentRun:
    return run_resolver_agent(FakeAgentLLM(*answers), make_resolver_tools(CATALOGUE, CATALOGUE, BAINS), "system", "line")


def test_the_conversation_is_kept_when_the_limit_is_hit() -> None:
    run = run_agent(call("search_catalogue", text="cartouche"))

    assert (run.proposal, run.stop_reason) == (None, "call_limit")
    assert sum(isinstance(m, ToolMessage) for m in run.messages) == MAX_MODEL_CALLS  # not only the 2 start messages


def test_the_proposal_is_returned_unchecked() -> None:
    run = run_agent(answer("CAR-99"))

    assert (run.stop_reason, run.proposal.code) == ("answered", "CAR-99")  # check_proposal is the caller's job


class BrokenErp:
    """The ERP times out on history, everything else works."""

    def __getattr__(self, name):
        return getattr(CATALOGUE, name)

    def past_orders(self, customer_id, limit):
        raise ConnectionError("ERP timeout on host erp.internal")


def test_a_failing_tool_is_an_answer_not_a_crash() -> None:
    agent = FakeAgentLLM(call("find_past_orders"), answer("NONE", "none", None))

    result = agent_line(agent, "system", SAME_AS_LAST_TIME, previous_order_line(), BAINS, BrokenErp(), CATALOGUE)

    reply = agent.llm.calls[1][-1]
    assert (reply.status, reply.content) == ("error", TOOL_FAILED)  # no internal details in the prompt
    assert (result.reason, result.next_step) == ("agent_failed", "human")


def test_an_answer_on_the_last_allowed_call_is_kept() -> None:
    searches = [call("search_catalogue", text=f"try {i}") for i in range(MAX_MODEL_CALLS - 1)]

    run = run_agent(*searches, answer("CAR-35"))

    assert (run.stop_reason, run.proposal.code) == ("answered", "CAR-35")
