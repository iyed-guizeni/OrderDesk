"""The Resolver agent metrics: pure functions, no network, unit-tested (tests/unit/test_resolver_metrics.py).

They judge the AGENT's own output (its proposal and its conversation), not the code checks that run after it.
The OUTCOME decides pass or fail; the behaviour metrics explain why.

Outcome of one case, from the agent's proposal:
  correct_product  the right product
  correct_human    NONE, and that was right (or acceptable: expected.accept_human)
  missed           NONE, but the product could be found                          -> costs a little time
  silent_wrong     a product, but the wrong one (or one where NONE was expected)  -> the critical one
"""

import json
import re
import statistics
from collections.abc import Sequence

from langchain_core.messages import AIMessage, ToolMessage

from orderdesk.agents.resolver_agent import FINAL_ANSWER, TOOL_FAILED, AgentRun
from orderdesk.catalogue.ports import ErpPort
from orderdesk.catalogue.text import code_key

# The bar: decided before running, never tuned to the results.
BAR_CORRECT = 0.80  # share of cases with a correct outcome
BAR_SILENT_WRONG = 0  # wrong products proposed: none allowed

CODE_LIKE = re.compile(r"\b[A-Za-z][A-Za-z0-9-]*\d[A-Za-z0-9-]*\b")  # PF60375, CAR-35, INV-3V (not 35mm)


# ---------- facts from one run ----------


def run_facts(run: AgentRun | None, seconds: float) -> dict:
    """What the agent did, as plain JSON: these are the experiment outputs in LangSmith. run=None: no LLM answered."""
    messages = run.messages if run else []
    answers = [m for m in messages if isinstance(m, AIMessage)]
    return {
        "stop_reason": run.stop_reason if run else "providers_failed",
        "proposal": run.proposal.model_dump() if run and run.proposal else None,
        "tool_calls": [{"name": c["name"], "args": c["args"]} for m in answers for c in m.tool_calls],
        "tool_replies": [{"name": m.name, "status": m.status, "content": str(m.content)}
                         for m in messages if isinstance(m, ToolMessage)],
        "model_calls": len(answers),
        "tokens": sum((m.usage_metadata or {}).get("total_tokens", 0) for m in answers),
        "seconds": round(seconds, 2),
    }


# ---------- one case ----------


def proposed_code(facts: dict) -> str | None:
    """The product the agent proposed. NONE or no proposal at all: it handed the line to a person."""
    proposal = facts["proposal"]
    if proposal is None or proposal["code"].strip().upper() == "NONE":
        return None
    return proposal["code"]


def outcome(expected: dict, code: str | None) -> str:
    want = expected.get("code")
    if code is not None:
        return "correct_product" if want and code_key(code) == code_key(want) else "silent_wrong"
    if want is None or expected.get("accept_human", False):
        return "correct_human"
    return "missed"


def hallucinated(facts: dict, erp: ErpPort, customer_id: int | None) -> bool:
    """The proposal names something that does not exist: a code, an order, or a code not in that order."""
    code = proposed_code(facts)
    if code is None:
        return False
    product = erp.get_product(code)
    if product is None:
        return True
    order_id = facts["proposal"].get("source_order_id")
    if not order_id:
        return False
    order = erp.get_order(customer_id, order_id) if customer_id is not None else None
    return order is None or product.code not in {line.code for line in order.lines}


def tools_called(facts: dict) -> list[str]:
    return [c["name"] for c in facts["tool_calls"] if c["name"] != FINAL_ANSWER]


def model_errors(facts: dict) -> int:
    """Error replies caused by the model: an invalid AgentProposal, an unknown tool, bad arguments.
    A tool that failed on our side (TOOL_FAILED) is not the model's fault."""
    return sum(r["status"] == "error" and r["content"] != TOOL_FAILED for r in facts["tool_replies"])


def repeated_calls(facts: dict) -> int:
    """Tool calls identical (same tool, same arguments) to an earlier one: wasted turns."""
    seen, repeats = set(), 0
    for call in facts["tool_calls"]:
        key = (call["name"], json.dumps(call["args"], sort_keys=True))
        repeats += key in seen
        seen.add(key)
    return repeats


def _codes_in(call: dict) -> list[str]:
    if call["name"] == "get_product":
        return [str(call["args"].get("code", ""))]
    if call["name"] == "search_catalogue":
        return CODE_LIKE.findall(str(call["args"].get("text", "")))
    return []


def invented_lookups(facts: dict, raw_text: str) -> int:
    """Codes in lookups (get_product, or a search) that appear neither in the client's line nor in an earlier tool
    result: the model made the parameter up instead of reading it.
    Example: the client wrote PF6037, the agent looked up PF60375 (it picked the missing digit itself)."""
    replies = iter(facts["tool_replies"])
    seen = code_key(raw_text)
    invented = 0
    for call in facts["tool_calls"]:
        invented += sum(code_key(code) not in seen for code in _codes_in(call))
        reply = next(replies, None)  # one reply per call, in the same order
        if reply:
            seen += " " + code_key(reply["content"])
    return invented


def score_case(inputs: dict, facts: dict, expected: dict, erp: ErpPort) -> dict[str, float | int | bool]:
    result = outcome(expected, proposed_code(facts))
    scores: dict[str, float | int | bool] = {
        "correct": result in ("correct_product", "correct_human"),
        "silent_wrong": result == "silent_wrong",
        "missed": result == "missed",
        "hallucinated": hallucinated(facts, erp, inputs.get("customer_id")),
        "required_tools": set(expected.get("required_tools", [])) <= set(tools_called(facts)),
        "model_errors": model_errors(facts),
        "invented_lookups": invented_lookups(facts, inputs["line"]["raw_text"]),
        "repeated_calls": repeated_calls(facts),
        "model_calls": facts["model_calls"],
        "within_budget": facts["model_calls"] <= expected["max_model_calls"],
        "hit_limit": facts["stop_reason"] == "call_limit",
        "providers_failed": facts["stop_reason"] == "providers_failed",
        "seconds": facts["seconds"],
        "tokens": facts["tokens"],
    }
    if inputs.get("fault"):
        scores["recovered"] = scores["correct"]  # correct, or a safe NONE where that is acceptable
    return scores


# ---------- the whole experiment ----------


def _p95(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))] if ordered else 0.0


def summarize(scores: Sequence[dict]) -> dict:
    """Experiment-level numbers LangSmith does not compute on its own (it shows the mean of each case score)."""
    n = len(scores)
    correct = round(sum(s["correct"] for s in scores) / n, 3) if n else 0.0
    silent_wrong = sum(s["silent_wrong"] for s in scores)
    faults = [s["recovered"] for s in scores if "recovered" in s]
    infra = sum(s["providers_failed"] for s in scores)  # an LLM provider failed: the run says nothing about the agent
    verdict = "RERUN" if infra else ("PASS" if correct >= BAR_CORRECT and silent_wrong <= BAR_SILENT_WRONG else "FAIL")
    return {
        "verdict": verdict,
        "correct_rate": correct,
        "silent_wrong_cases": silent_wrong,
        "recovered_rate": round(sum(faults) / len(faults), 3) if faults else None,
        "invented_lookups": sum(s["invented_lookups"] for s in scores),
        "model_calls_median": statistics.median([s["model_calls"] for s in scores]) if n else 0,
        "model_calls_p95": _p95([s["model_calls"] for s in scores]),
        "seconds_median": round(statistics.median([s["seconds"] for s in scores]), 2) if n else 0,
        "seconds_p95": _p95([s["seconds"] for s in scores]),
        "tokens_median": statistics.median([s["tokens"] for s in scores]) if n else 0,
    }
