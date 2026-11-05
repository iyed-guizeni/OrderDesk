

import hashlib
import json
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from langsmith import traceable
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

from orderdesk.domain.classification import EmailClassification
from orderdesk.domain.email import EmailContext
from orderdesk.profiles.ondyna import ONDYNA_PROFILE
from orderdesk.prompts.classification import build_system_prompt
from orderdesk.workflow.classification import make_classify_node
from langchain_openrouter import ChatOpenRouter



DATASET = Path(__file__).parent / "classify" / "ondyna.jsonl"
RESULTS_DIR = Path(__file__).parent / "results" / "classify"
#MODEL = "gemini-3.5-flash-lite"
PAUSE_SECONDS = 3  # free tiers limit requests per minute
#MODEL = "nvidia/nemotron-3.5-lightning:free"
#MODEL="gemini-3.8-flash"#high demande
MODEL="gemini-3.6-flash"


def run_case(classify, case: dict) -> dict:
    """Classify one email and return what happened (never raises)."""
    expected = case["expected"]
    row = {
        "id": case["id"],
        "expected_label": expected["label"],
        "expected_also_contains": expected.get("also_contains", []),
        "label": None,
        "also_contains": [],
        "confidence": None,
        "evidence": None,
        "error": None,
        "note": case.get("note", ""),
    }
    start = time.perf_counter()
    try:
        answer = classify({"email": EmailContext.model_validate(case["email"])})["classification"]
        row.update(label=answer.label, also_contains=list(answer.also_contains),
                   confidence=answer.confidence, evidence=answer.evidence)
    except Exception as error:  # rate limit, invalid answer...: recorded, the run continues
        row["error"] = f"{type(error).__name__}: {' '.join(str(error).split())}"
    row["seconds"] = round(time.perf_counter() - start, 2)
    row["correct"] = row["label"] in [expected["label"], *expected.get("acceptable_labels", [])]
    row["also_contains_correct"] = sorted(row["also_contains"]) == sorted(row["expected_also_contains"])
    return row


def metrics(rows: list[dict]) -> dict:
    per_label = defaultdict(lambda: {"correct": 0, "total": 0})
    for row in rows:
        per_label[row["expected_label"]]["total"] += 1
        per_label[row["expected_label"]]["correct"] += row["correct"]
    correct = sum(row["correct"] for row in rows)
    return {
        "accuracy": round(correct / len(rows), 3),
        "correct": correct,
        "total": len(rows),
        "per_label": {label: f"{v['correct']}/{v['total']}" for label, v in sorted(per_label.items())},
        "also_contains_correct": sum(row["also_contains_correct"] for row in rows),
        "errors": sum(row["error"] is not None for row in rows),
        "mistakes": [row["id"] for row in rows if not row["correct"]],
    }

load_dotenv()

@traceable
def main() -> None:

    system_prompt = build_system_prompt(ONDYNA_PROFILE)
    llm = ChatGoogleGenerativeAI(model=MODEL, temperature=0).with_structured_output(EmailClassification)
    classify = make_classify_node(llm, ONDYNA_PROFILE)
    cases = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]

    rows = []
    for case in cases:
        row = run_case(classify, case)
        rows.append(row)
        print(f"{'ok ' if row['correct'] else 'BAD'} {row['id']:<10} "
              f"expected {row['expected_label']:<13} got {row['label'] or row['error']}")
        time.sleep(PAUSE_SECONDS)

    result = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "model": MODEL,
        "dataset": DATASET.name,
        # Fingerprint of the exact system prompt: two runs with the same value used the same prompt.
        "prompt_sha": hashlib.sha256(system_prompt.encode()).hexdigest()[:10],
        "metrics": metrics(rows),
        "cases": rows,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_model = MODEL.replace("/", "_").replace(":", "_")
    run_file = RESULTS_DIR / f"{datetime.now():%Y-%m-%d_%H%M%S}_{safe_model}.json"    
    run_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {key: result[key] for key in ("date", "model", "dataset", "prompt_sha")} | {
        key: result["metrics"][key] for key in ("accuracy", "correct", "total", "per_label", "errors")
    } | {"file": run_file.name}
    with (RESULTS_DIR / "history.jsonl").open("a", encoding="utf-8") as history:
        history.write(json.dumps(summary, ensure_ascii=False) + "\n")

    m = result["metrics"]
    print(f"\nAccuracy: {m['correct']}/{m['total']} ({m['accuracy']:.0%})   errors: {m['errors']}")
    for label, score in m["per_label"].items():
        print(f"  {label:<14} {score}")
    for row in rows:
        if not row["correct"]:
            print(f"\n- {row['id']}: expected {row['expected_label']}, got {row['label'] or row['error']}"
                  f"\n  evidence: {row['evidence']}\n  note: {row['note']}")
    print(f"\nSaved: {run_file}")


if __name__ == "__main__":
    main()