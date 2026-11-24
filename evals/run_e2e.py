"""Does a whole email become the right draft lines? Classification, extraction, the ladder, the choice step
and the Resolver agent together, on the emails of evals/e2e/ondyna.jsonl, as a LangSmith experiment.

    venv\\Scripts\\python -m evals.run_e2e                                   # first model of the chain
    venv\\Scripts\\python -m evals.run_e2e --model gemini:gemini-3.5-flash-lite
    venv\\Scripts\\python -m evals.run_e2e --only e2e-03,e2e-09              # a few emails while debugging

One trace per email, with every step of the graph inside it (the agent runs nest under it too).
The scores are those of e2e_metrics.py; the per-step evals (classify, extract, resolver) say WHICH step failed.
Synthetic data only: the traces contain the email, its (invented) sender and the prompts.
"""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from langsmith import Client

from evals.e2e_metrics import score_email, summarize
from orderdesk.domain.classification import EmailClassification
from orderdesk.domain.email import EmailContext
from orderdesk.domain.extraction import ExtractedOrder
from orderdesk.domain.resolution import CandidateChoice
from orderdesk.llm.client import AllProvidersFailed, make_fallback_chat, make_fallback_llm
from orderdesk.profiles.demo_catalogue import make_demo_catalogue
from orderdesk.profiles.ondyna import ONDYNA_PROFILE
from orderdesk.workflow.graph import build_graph
from orderdesk.workflow.state import WorkflowState
from orderdesk.workflow.studio import PROVIDER_CHAIN

CASES = Path(__file__).parent / "e2e" / "ondyna.jsonl"
TENANT_ID = 999  # the eval tenant


class NoFiles:
    """The e2e emails have no attachments: any read is a bug in a case."""

    def put(self, tenant_id: int, kind: str, data: bytes) -> str:
        raise NotImplementedError("the eval never stores files")

    def get(self, key: str) -> bytes:
        raise KeyError(key)


def load_cases() -> list[dict]:
    return [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip()]


def upload_dataset(client: Client, cases: list[dict]) -> str:
    """One dataset per version of the case file, like evals/run_resolver.py."""
    digest = hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest()[:8]
    name = f"orderdesk-e2e-{digest}"
    if not client.has_dataset(dataset_name=name):
        dataset = client.create_dataset(name, description=f"Whole emails from {CASES.name} ({len(cases)})")
        client.create_examples(dataset_id=dataset.id, examples=[
            {"inputs": {"email": case["email"]}, "outputs": case["expected"],
             "metadata": {"case": case["id"], "difficulty": case["difficulty"], "note": case["note"]}}
            for case in cases
        ])
    return name


def build_eval_graph(provider: str, model: str):
    """The production graph, one model for every step (no fallback: one model per experiment)."""
    chain = [(provider, model)]
    catalogue = make_demo_catalogue()  # fresh per run: nothing carries over between experiments
    return build_graph(
        classify_llm=make_fallback_llm(chain, EmailClassification),
        extract_llm=make_fallback_llm(chain, ExtractedOrder),
        choose_llm=make_fallback_llm(chain, CandidateChoice),
        agent_llm=make_fallback_chat(chain),
        profile=ONDYNA_PROFILE, storage=NoFiles(), erp=catalogue, search=catalogue, aliases=catalogue,
    )


def facts(state: dict, seconds: float) -> dict:
    """What the workflow produced, in plain values: the label, the PO, and per line the product or who decides."""
    extracted, resolved = state.get("extracted_order"), state.get("resolved_order")
    lines = []
    if extracted is not None and resolved is not None:
        for resolution in resolved.lines:
            lines.append({
                "raw_text": extracted.lines[resolution.line_index].raw_text,
                "code": resolution.product.code if resolution.product else None,
                "method": resolution.method, "reason": resolution.reason, "next_step": resolution.next_step,
                "evidence": resolution.evidence,
            })
    return {"label": state["classification"].label, "po_number": extracted.po_number if extracted else None,
            "lines": lines, "grounding_issues": [issue.kind for issue in state.get("grounding_issues", [])],
            "providers_failed": False, "seconds": round(seconds, 2)}


def make_target(graph):
    def workflow_email(inputs: dict) -> dict:
        email = EmailContext(tenant_id=TENANT_ID, email_id=1, **inputs["email"])
        start = time.perf_counter()
        try:
            state = graph.invoke(WorkflowState(email=email), {"run_name": "orderdesk_workflow"})
        except AllProvidersFailed:
            return {"providers_failed": True, "seconds": round(time.perf_counter() - start, 2)}
        return facts(state, time.perf_counter() - start)

    return workflow_email


def email_scores(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    return {"results": [{"key": key, "score": value} for key, value in score_email(outputs, reference_outputs).items()
                        if value is not None]}


def experiment_summary(inputs: list[dict], outputs: list[dict], reference_outputs: list[dict]) -> dict:
    summary = summarize([score_email(o, r) for o, r in zip(outputs, reference_outputs)])
    return {"results": [
        {"key": key, "value": value} if isinstance(value, str) else {"key": key, "score": value}
        for key, value in summary.items() if value is not None
    ]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    first = f"{PROVIDER_CHAIN[0][0]}:{PROVIDER_CHAIN[0][1]}"
    parser.add_argument("--model", default=first, help="provider:model for every step (no fallback)")
    parser.add_argument("--only", help="comma-separated case ids")
    args = parser.parse_args()

    load_dotenv(Path(__file__).parents[1] / ".env")
    # RESOLVER_AGENT_TRACING=true makes each agent run its OWN trace (for production, where the workflow is not
    # traced). Here the whole email is one trace: the agent runs must stay inside it.
    os.environ["RESOLVER_AGENT_TRACING"] = "false"

    provider, model = args.model.split(":", 1)
    cases = load_cases()
    client = Client()
    dataset = upload_dataset(client, cases)
    only = set(args.only.split(",")) if args.only else None
    examples = [e for e in client.list_examples(dataset_name=dataset) if not only or e.metadata["case"] in only]
    results = client.evaluate(
        make_target(build_eval_graph(provider, model)),
        data=examples,
        evaluators=[email_scores],
        summary_evaluators=[experiment_summary],
        experiment_prefix=f"e2e-{model.split('/')[-1]}",
        metadata={"model": args.model},
        max_concurrency=0,  # one email at a time: free tiers limit requests per minute
    )
    rows = [row for row in results if row["run"].outputs]  # a crashed email has no outputs: its trace shows why
    scored = [(row["example"].metadata["case"], row["run"].outputs, score_email(row["run"].outputs,
                                                                              row["example"].outputs))
              for row in rows]
    print(f"\nexperiment {results.experiment_name}: {len(rows)} emails scored, open it in LangSmith for the traces")
    for key, value in summarize([s for _, _, s in scored]).items():
        print(f"  {key:<20} {value}")
    print_wrong_emails(scored, {row["example"].metadata["case"]: row["example"].outputs for row in rows})


def print_wrong_emails(scored: list[tuple[str, dict, dict]], expected: dict[str, dict]) -> None:
    """Which line went wrong, and how far it got: enough to open the right trace."""
    for case, outputs, scores in sorted(scored):
        if scores.get("email_correct", True) or scores.get("providers_failed"):
            continue
        want = expected[case]
        print(f"\n  {case}: label {outputs['label']} (want {want['label']}), PO {outputs['po_number']!r} "
              f"(want {want['po_number']!r})")
        for i, line in enumerate(outputs["lines"]):
            target = want["lines"][i]["code"] if i < len(want["lines"]) else "(not in the email)"
            mark = "ok" if line["code"] == target else "WRONG" if line["code"] else "-> person"
            print(f"    {mark:<9} {line['raw_text']!r}: got {line['code']} via {line['method'] or line['reason']},"
                  f" want {target}")


if __name__ == "__main__":
    main()
