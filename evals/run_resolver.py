"""Does the Resolver agent find the right product, or hand the line to a person when it should?

Runs the agent ALONE (not the whole graph, not the code checks after it) on the cases of
evals/resolver/ondyna.jsonl, as a LangSmith experiment: one trace per case, the scores of resolver_metrics.py
on each trace, and a summary.

    venv\\Scripts\\python -m evals.run_resolver                      # first model of the chain
    venv\\Scripts\\python -m evals.run_resolver --model gemini:gemini-3.5-flash-lite
    venv\\Scripts\\python -m evals.run_resolver --only po-01,vm-05   # a few cases while debugging
    venv\\Scripts\\python -m evals.run_resolver --repeat             # REPEAT_CASES, 3 runs each: is it stable?

Each case holds exactly what the agent receives: the line, the reason, the candidates (codes of the demo
catalogue) and the note of the earlier step. tests/unit/test_resolver_metrics.py checks that the earlier steps
would really send each case to the agent with these candidates.
Synthetic data only: the traces contain the prompts and tool results.
"""

import argparse
import hashlib
import json
import time
from pathlib import Path

from dotenv import load_dotenv
from langsmith import Client

from evals.resolver_metrics import run_facts, score_case, summarize
from orderdesk.agents.resolver_agent import MAX_MODEL_CALLS, run_resolver_agent
from orderdesk.agents.resolver_tools import make_resolver_tools
from orderdesk.catalogue.memory import MemoryCatalogue
from orderdesk.domain.catalogue import Candidate
from orderdesk.domain.extraction import ExtractedLine
from orderdesk.domain.resolution import LineResolution
from orderdesk.llm.client import AllProvidersFailed, make_fallback_chat
from orderdesk.profiles.demo_catalogue import DEMO_CUSTOMERS, make_demo_catalogue
from orderdesk.profiles.ondyna import ONDYNA_PROFILE
from orderdesk.prompts.resolver_agent import build_system_prompt, build_user_prompt
from orderdesk.workflow.studio import PROVIDER_CHAIN

CASES = Path(__file__).parent / "resolver" / "ondyna.jsonl"
CATALOGUE = make_demo_catalogue()
CUSTOMERS = {customer.id: customer for customer in DEMO_CUSTOMERS.values()}

# --repeat: one or two cases per kind, and every case that once flipped between runs. 10 cases x 3 runs
# stays within the free tiers; the full file x 3 does not.
REPEAT_CASES = ["po-01", "po-07", "vm-01", "vm-05", "nc-06", "nc-08", "nc-09", "ls-02", "ls-04", "fr-02"]
REPETITIONS = 3


# ---------- the dataset ----------


def load_cases() -> list[dict]: 
    return [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip()]


def upload_dataset(client: Client, cases: list[dict]) -> str:
    """One dataset per version of the case file: a changed case never mixes with old experiments."""
    digest = hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest()[:8]
    name = f"orderdesk-resolver-{digest}"
    if not client.has_dataset(dataset_name=name):
        dataset = client.create_dataset(name, description=f"Resolver agent cases from {CASES.name} ({len(cases)})")
        client.create_examples(dataset_id=dataset.id, examples=[
            {"inputs": case["input"], "outputs": case["expected"],
             "metadata": {"case": case["id"], "difficulty": case["difficulty"], "reason": case["input"]["reason"],
                          "note": case["note"], "fault": bool(case["input"].get("fault"))}}
            for case in cases
        ])
    return name


# ---------- injected faults ----------


class FaultyCatalogue:
    """The demo catalogue with ONE tool broken: error_once, error_always, or empty (no history)."""

    METHODS = {"find_past_orders": "past_orders", "search_catalogue": "search"}

    def __init__(self, catalogue: MemoryCatalogue, fault: dict) -> None:
        self._catalogue = catalogue
        self._method = self.METHODS[fault["tool"]]
        self._kind = fault["kind"]
        self._calls = 0

    def __getattr__(self, name: str):
        real = getattr(self._catalogue, name)
        if name != self._method:
            return real

        def broken(*args, **kwargs):
            self._calls += 1
            if self._kind == "empty":
                return []
            if self._kind == "error_always" or self._calls == 1:
                raise ConnectionError("synthetic fault: the ERP did not answer")
            return real(*args, **kwargs)

        return broken


# ---------- the target: one case -> the agent -> facts ----------


def starting_point(inputs: dict) -> tuple[ExtractedLine, LineResolution]:
    """The line and the unresolved LineResolution the agent receives, read from the case as written."""
    line = ExtractedLine.model_validate(inputs["line"])
    candidates = [Candidate(product=CATALOGUE.get_product(code), score=1.0)  # the agent never sees the score
                  for code in inputs.get("candidates", [])]
    resolution = LineResolution(line_index=0, status="unresolved", reason=inputs["reason"], candidates=candidates,
                                evidence=inputs.get("earlier_note"), next_step="agent")
    return line, resolution


def make_target(llm, system_prompt: str):
    def resolver_agent_case(inputs: dict) -> dict:
        line, resolution = starting_point(inputs)
        catalogue = FaultyCatalogue(CATALOGUE, inputs["fault"]) if inputs.get("fault") else CATALOGUE
        tools = make_resolver_tools(catalogue, catalogue, CUSTOMERS.get(inputs["customer_id"]))
        start = time.perf_counter()
        try:
            run = run_resolver_agent(llm, tools, system_prompt, build_user_prompt(line, resolution))
        except AllProvidersFailed:
            run = None  # scored as providers_failed: the verdict says RERUN
        return run_facts(run, time.perf_counter() - start)

    return resolver_agent_case


# ---------- evaluators: resolver_metrics, in LangSmith's format ----------


def case_scores(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    scores = score_case(inputs, outputs, reference_outputs, CATALOGUE)
    return {"results": [{"key": key, "score": value} for key, value in scores.items()]}


def experiment_summary(inputs: list[dict], outputs: list[dict], reference_outputs: list[dict]) -> dict:
    summary = summarize([score_case(i, o, r, CATALOGUE) for i, o, r in zip(inputs, outputs, reference_outputs)])
    return {"results": [
        {"key": key, "value": value} if isinstance(value, str) else {"key": key, "score": value}
        for key, value in summary.items() if value is not None
    ]}


# ---------- main ----------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    first = f"{PROVIDER_CHAIN[0][0]}:{PROVIDER_CHAIN[0][1]}"
    parser.add_argument("--model", default=first, help="provider:model, one model per experiment (no fallback)")
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--repeat", action="store_true",
                        help=f"run each case {REPETITIONS} times, in one experiment (default cases: REPEAT_CASES)")
    args = parser.parse_args()

    load_dotenv(Path(__file__).parents[1] / ".env")  # the API keys: found from any folder the script runs in

    provider, model = args.model.split(":", 1)
    llm = make_fallback_chat([(provider, model)])
    system_prompt = build_system_prompt(ONDYNA_PROFILE, MAX_MODEL_CALLS)
    cases = load_cases()
    for case in cases:  # fail fast on a broken case, before spending any LLM call
        starting_point(case["input"])

    client = Client()
    dataset = upload_dataset(client, cases)  # always the whole file: --only picks examples from it
    only = set(args.only.split(",")) if args.only else set(REPEAT_CASES) if args.repeat else None
    examples = [e for e in client.list_examples(dataset_name=dataset) if not only or e.metadata["case"] in only]
    repetitions = REPETITIONS if args.repeat else 1
    results = client.evaluate(
        make_target(llm, system_prompt),
        data=examples,
        evaluators=[case_scores],
        summary_evaluators=[experiment_summary],
        experiment_prefix=f"resolver-{model.split('/')[-1]}" + ("-repeat" if args.repeat else ""),
        metadata={"model": args.model, "max_model_calls": MAX_MODEL_CALLS, "repetitions": repetitions,
                  "prompt": hashlib.sha256(system_prompt.encode()).hexdigest()[:8]},
        num_repetitions=repetitions,  # one experiment, each example run N times
        max_concurrency=0,  # one case at a time: free tiers limit requests per minute
    )
    rows = [row for row in results if row["run"].outputs]  # a crashed case has no outputs: its trace shows why
    scores = [(row["example"].metadata["case"],
               score_case(row["example"].inputs, row["run"].outputs, row["example"].outputs, CATALOGUE))
              for row in rows]
    summary = summarize([s for _, s in scores])
    print(f"\nexperiment {results.experiment_name}: {len(rows)} runs scored, open it in LangSmith for the traces")
    for key, value in summary.items():
        print(f"  {key:<20} {value}")
    if repetitions > 1:
        print_stability(scores)


def print_stability(scores: list[tuple[str, dict]]) -> None:
    """Per case: how many runs were correct. A case that is not N/N is noise to fix, not one lucky run."""
    runs: dict[str, list[dict]] = {}
    for case, one_run in scores:
        runs.setdefault(case, []).append(one_run)
    print("\n  case    correct  silent wrong")
    for case, case_runs in sorted(runs.items()):
        correct = sum(s["correct"] for s in case_runs)
        wrong = sum(s["silent_wrong"] for s in case_runs)
        flag = "" if correct == len(case_runs) else "   <- unstable"
        print(f"  {case:<8}{correct}/{len(case_runs):<7}{wrong}{flag}")


if __name__ == "__main__":
    main()
