"""End-to-end metrics: one email through the whole workflow, scored on what a sales assistant gets.

Pure functions: no LLM, no LangSmith. evals/run_e2e.py runs the graph and calls them.
Lines are compared in order (line i of the email with expected line i). A line is right when its product is the
expected one, or when no product was expected and the line went to a person.
"""

from statistics import median

from orderdesk.catalogue.text import code_key

# The bar: decided before running, never tuned to the results.
BAR_LINES_CORRECT = 0.80
BAR_SILENT_WRONG = 0  # a wrong product on a draft quotation is the one mistake we never accept


def _same_po(expected: str | None, got: str | None) -> bool:
    """'BC-2290' and 'PO 2290' count as 2290."""
    if expected is None or got is None:
        return expected == got
    return code_key(expected) in code_key(got)


def score_email(outputs: dict, expected: dict) -> dict:
    if outputs.get("providers_failed"):
        return {"providers_failed": True, "seconds": outputs.get("seconds")}
    want = [line["code"] for line in expected["lines"]]
    got = [line["code"] for line in outputs["lines"]]
    pairs = list(zip(want, got))
    extra = got[len(want):]  # lines the client did not write
    correct = sum(w == g for w, g in pairs)
    silent_wrong = sum(g is not None and g != w for w, g in pairs) + sum(g is not None for g in extra)
    label_correct = outputs["label"] == expected["label"]
    po_correct = _same_po(expected.get("po_number"), outputs.get("po_number"))
    return {
        "label_correct": label_correct,
        "po_correct": po_correct,
        "lines_expected": len(want),
        "lines_found": len(got),
        "lines_correct": correct,
        "silent_wrong": silent_wrong,
        "missed": sum(w is not None and g is None for w, g in pairs) + max(0, len(want) - len(got)),
        "lines_automatic": sum(g is not None for g in got[:len(want)]),
        "email_correct": label_correct and po_correct and len(got) == len(want) and correct == len(want),
        "providers_failed": False,
        "seconds": outputs["seconds"],
    }


def _p95(values: list[float]) -> float | None:
    return sorted(values)[max(0, round(0.95 * len(values)) - 1)] if values else None


def summarize(scores: list[dict]) -> dict:
    ran = [s for s in scores if not s["providers_failed"]]
    lines = sum(s["lines_expected"] for s in ran)
    line_rate = sum(s["lines_correct"] for s in ran) / lines if lines else 0.0
    silent_wrong = sum(s["silent_wrong"] for s in ran)
    labels_ok = all(s["label_correct"] for s in ran)
    if len(ran) < len(scores):
        verdict = "RERUN"  # a provider failed: this run says nothing about the workflow
    elif silent_wrong > BAR_SILENT_WRONG or line_rate < BAR_LINES_CORRECT or not labels_ok:
        verdict = "FAIL"
    else:
        verdict = "PASS"
    seconds = [s["seconds"] for s in ran]
    return {
        "verdict": verdict,
        "emails_correct_rate": round(sum(s["email_correct"] for s in ran) / len(ran), 3) if ran else None,
        "lines_correct_rate": round(line_rate, 3),
        "silent_wrong_lines": silent_wrong,
        "missed_lines": sum(s["missed"] for s in ran),
        "automatic_rate": round(sum(s["lines_automatic"] for s in ran) / lines, 3) if lines else None,
        "label_accuracy": round(sum(s["label_correct"] for s in ran) / len(ran), 3) if ran else None,
        "po_accuracy": round(sum(s["po_correct"] for s in ran) / len(ran), 3) if ran else None,
        "seconds_median": round(median(seconds), 2) if seconds else None,
        "seconds_p95": _p95(seconds),
    }
