"""Does each model extract what a sales assistant needs? 

"""

import argparse
import hashlib
import io
import json
import time
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from orderdesk.domain.email import AttachmentRef, EmailContext
from orderdesk.domain.extraction import ExtractedLine, ExtractedOrder
from orderdesk.llm.client import AllProvidersFailed, make_fallback_llm
from orderdesk.profiles.ondyna import ONDYNA_PROFILE
from orderdesk.prompts.extract_order_info import build_system_prompt
from orderdesk.workflow.new_order import make_extract_node
from orderdesk.workflow.state import WorkflowState
from orderdesk.workflow.studio import PROVIDER_CHAIN

DATASET = Path(__file__).parent / "extract" / "ondyna.jsonl"
RESULTS_DIR = Path(__file__).parent / "results" / "extract"
PAUSE_SECONDS = 3  # free tiers limit requests per minute
API_RETRY_PAUSE_SECONDS = 60  # before retrying the cases that hit an API error
RECHECK_RUNS = 2  # extra runs for each failed case
MAX_RECHECK_CASES = 10  # a model failing more cases than this fails the bar anyway: save the quota

# The bar: decided before running, never tuned to the results.
BAR_CRITICAL_CLEAN = 0.95  # share of cases with no critical mistake
BAR_MINOR_FIELDS = 0.85  # share of minor checks that are right

CRITICAL_HEADER = ("po_number", "order_reference", "attachment_mentioned")
MINOR_HEADER = ("delivery_kind", "requested_date")
CRITICAL_LINE = ("quantity", "kind")
MINOR_LINE = ("unit", "price")
PO_PREFIXES = {"", "PO", "BC", "N", "NO", "PON", "BCN"}  # "PO#77120" and "BC n° 5133" count as the number itself


class MemoryStorage:
    """Storage in memory: key -> bytes."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def put(self, tenant_id: int, kind: str, data: bytes) -> str:
        raise NotImplementedError("the eval only reads attachments")

    def get(self, key: str) -> bytes:
        return self.files[key]


# ---------- building the input ----------


def _pdf_bytes(text: str) -> bytes:
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    for number, line in enumerate(text.splitlines()):
        pdf.drawString(72, 780 - 16 * number, line)
    pdf.save()
    return buffer.getvalue()


def _xlsx_bytes(rows: list[list]) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _attachment_bytes(spec: dict) -> bytes:
    if "rows" in spec:
        return _xlsx_bytes(spec["rows"])
    if spec["text"] is None:  # a photo or a scanned PDF: no text to read
        return b"\xff\xd8\xff\xe0 synthetic image bytes"
    if spec["content_type"] == "application/pdf":
        return _pdf_bytes(spec["text"])
    return spec["text"].encode(spec.get("encoding", "utf-8"))


def build_input(case: dict, email_id: int) -> tuple[WorkflowState, MemoryStorage]:
    files, refs = {}, []
    for spec in case["attachments"]:
        key = f"eval/{case['id']}/{spec['filename']}"
        files[key] = _attachment_bytes(spec)
        refs.append(AttachmentRef(filename=spec["filename"], content_type=spec["content_type"], storage_key=key))
    email = EmailContext(tenant_id=999, email_id=email_id, attachments=refs, **case["email"])
    return WorkflowState(email=email), MemoryStorage(files)


# ---------- scoring ----------


def _norm(text: str | None) -> str:
    """Lower case, no accents, single spaces: 'Noir  Brossé' -> 'noir brosse'."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return " ".join(text.lower().split())


def _alnum(text: str | None) -> str:
    return "".join(ch for ch in _norm(text) if ch.isalnum()).upper()


def _same_po(expected: str | None, got: str | None) -> bool:
    if expected is None or got is None:
        return expected == got
    want, have = _alnum(expected), _alnum(got)
    return have.endswith(want) and have[: len(have) - len(want)] in PO_PREFIXES


def _same(field: str, want, have) -> bool:
    if field == "po_number":
        return _same_po(want, have)
    if field in ("quantity", "price") and want is not None and have is not None:
        return abs(want - have) < 1e-6
    return want == have


def _header_values(order: ExtractedOrder) -> dict:
    date = order.requested_date.value if order.requested_date else None
    return {
        "po_number": order.po_number,
        "attachment_mentioned": order.attachment_mentioned,
        "order_reference": order.order_reference is not None,
        "delivery_kind": order.delivery.kind if order.delivery else None,
        "requested_date": date.isoformat() if date else None,
    }


def _line_values(line: ExtractedLine) -> dict:
    return {"quantity": line.quantity, "unit": line.unit, "kind": line.product.kind, "price": line.price_mentioned}


def _matches(spec: dict, line: ExtractedLine) -> bool:
    """Is this the line the case describes? Identified by code, product words and variant (or quantity alone)."""
    words = _norm(f"{line.product.text} {line.product.variant_text}")
    if "code" in spec and _alnum(spec["code"]) != _alnum(line.product.code_as_written):
        return False
    if "text" in spec and _norm(spec["text"]) not in words:
        return False
    if "variant" in spec and _norm(spec["variant"]) not in words:
        return False
    if not {"code", "text", "variant"} & spec.keys():
        return line.quantity == spec.get("quantity")
    return True


def score(expected: dict, order: ExtractedOrder) -> dict:
    """Every check as (name, ok), split into critical and minor; plus what went wrong, in words."""
    critical, minor, problems = [], [], []
    header = _header_values(order)
    for fields, bucket in ((CRITICAL_HEADER, critical), (MINOR_HEADER, minor)):
        for field in fields:
            if field in expected:
                ok = _same(field, expected[field], header[field])
                bucket.append((field, ok))
                if not ok:
                    problems.append(f"{field}: expected {expected[field]!r}, got {header[field]!r}")

    unused = list(order.lines)
    for spec in expected["lines"]:
        found = next((line for line in unused if _matches(spec, line)), None)
        critical.append(("line_found", found is not None))
        if found is None:
            problems.append(f"line not found: {spec}")
            continue
        unused.remove(found)
        got = _line_values(found)
        for fields, bucket in ((CRITICAL_LINE, critical), (MINOR_LINE, minor)):
            for field in fields:
                if field in spec:
                    ok = _same(field, spec[field], got[field])
                    bucket.append((f"line.{field}", ok))
                    if not ok:
                        problems.append(f"line {found.raw_text!r}: {field} expected {spec[field]!r}, got {got[field]!r}")

    invented = [line.raw_text for line in unused]
    critical.append(("no_invented_line", not invented))
    problems += [f"invented or unmatched line: {text!r}" for text in invented]
    return {
        "critical_ok": all(ok for _, ok in critical),
        "perfect": all(ok for _, ok in critical + minor),
        "critical": critical,
        "minor": minor,
        "invented_lines": len(invented),
        "problems": problems,
    }


# ---------- running ----------


def _is_api_error(error: Exception) -> bool:
    """True when the provider failed (rate limit, timeout, down). False when the model gave a bad answer."""
    if isinstance(error, AllProvidersFailed):
        return not all(" invalid: " in message for message in error.errors)
    return not isinstance(error, ValueError)  # ValueError = the node got no valid ExtractedOrder


def run_case(extract, case: dict, email_id: int) -> dict:
    """Extract one email and score it (never raises). status: ok | invalid_answer | api_error."""
    row = {"id": case["id"], "difficulty": case["difficulty"], "note": case["note"], "error": None}
    start = time.perf_counter()
    try:
        state, storage = build_input(case, email_id)
        order = extract(state, storage)
        row.update(status="ok", answer=order.model_dump(mode="json"), **score(case["expected"], order))
    except Exception as error:
        row["error"] = f"{type(error).__name__}: {' '.join(str(error).split())}"[:500]
        if _is_api_error(error):
            row.update(status="api_error")
        else:  # the model answered with something unusable: a critical failure
            row.update(status="invalid_answer", critical_ok=False, perfect=False, critical=[], minor=[],
                       invented_lines=0, problems=["the model gave no valid ExtractedOrder"])
    row["seconds"] = round(time.perf_counter() - start, 2)
    return row


def _print_row(row: dict) -> None:
    mark = "API" if row["status"] == "api_error" else "ok " if row["perfect"] else "BAD"
    print(f"{mark} {row['id']:<4} {row['difficulty']:<7} {row['seconds']:>6.1f}s  {row['note']}")


def recheck(extract, case: dict, email_id: int, runs: int) -> str:
    """Rerun a failed case. 'consistent' = fails every time (a real weakness), 'flaky' = sometimes passes."""
    outcomes = []
    for _ in range(runs):
        row = run_case(extract, case, email_id)
        if row["status"] != "api_error":
            outcomes.append(row["perfect"])
        time.sleep(PAUSE_SECONDS)
    if not outcomes:
        return "unknown (API errors)"
    return "flaky" if any(outcomes) else "consistent"


def metrics(rows: list[dict]) -> dict:
    scored = [row for row in rows if row["status"] != "api_error"]
    per_difficulty = defaultdict(lambda: [0, 0])
    fields = defaultdict(lambda: [0, 0])
    minor_ok = minor_total = 0
    for row in scored:
        per_difficulty[row["difficulty"]][0] += row["critical_ok"]
        per_difficulty[row["difficulty"]][1] += 1
        for name, ok in row["critical"] + row["minor"]:
            fields[name][0] += ok
            fields[name][1] += 1
        minor_ok += sum(ok for _, ok in row["minor"])
        minor_total += len(row["minor"])
    critical_clean = sum(row["critical_ok"] for row in scored)
    return {
        "scored": len(scored),
        "total": len(rows),
        "api_errors": len(rows) - len(scored),
        "invalid_answers": sum(row["status"] == "invalid_answer" for row in scored),
        "critical_clean": critical_clean,
        "critical_clean_rate": round(critical_clean / len(scored), 3) if scored else 0.0,
        "perfect": sum(row["perfect"] for row in scored),
        "minor_fields_rate": round(minor_ok / minor_total, 3) if minor_total else 1.0,
        "invented_lines": sum(row["invented_lines"] for row in scored),
        "critical_clean_per_difficulty": {k: f"{ok}/{n}" for k, (ok, n) in sorted(per_difficulty.items())},
        "fields": {name: f"{ok}/{n}" for name, (ok, n) in sorted(fields.items())},
        "mean_seconds": round(sum(row["seconds"] for row in scored) / len(scored), 2) if scored else None,
    }


def verdict(m: dict, rows: list[dict]) -> tuple[str, list[str]]:
    """PASS / FAIL / INCOMPLETE, with the reasons."""
    reasons = []
    if m["critical_clean_rate"] < BAR_CRITICAL_CLEAN:
        reasons.append(f"critical-clean cases {m['critical_clean_rate']:.0%} < {BAR_CRITICAL_CLEAN:.0%}")
    easy_failed = [row["id"] for row in rows if row["difficulty"] == "easy" and row["status"] != "api_error"
                   and not row["critical_ok"]]
    if easy_failed:
        reasons.append(f"critical mistakes on easy cases: {', '.join(easy_failed)}")
    if m["invented_lines"]:
        reasons.append(f"{m['invented_lines']} invented line(s)")
    if m["minor_fields_rate"] < BAR_MINOR_FIELDS:
        reasons.append(f"minor fields {m['minor_fields_rate']:.0%} < {BAR_MINOR_FIELDS:.0%}")
    if reasons:
        return "FAIL", reasons
    if m["api_errors"]:
        return "INCOMPLETE", [f"{m['api_errors']} case(s) not scored because of API errors: run again later"]
    return "PASS", []


def evaluate_model(provider: str, model: str, cases: list[dict], system_prompt: str, recheck_runs: int) -> dict:
    llm = make_fallback_llm([(provider, model)], ExtractedOrder)  # one model at a time; retries 429/timeouts

    def extract(state: WorkflowState, storage: MemoryStorage) -> ExtractedOrder:
        return make_extract_node(llm, ONDYNA_PROFILE, storage)(state)["extracted_order"]

    print(f"\n=== {provider}:{model} ===")
    email_ids = {case["id"]: 3000 + number for number, case in enumerate(cases)}
    by_id = {case["id"]: case for case in cases}
    rows = []
    for case in cases:
        rows.append(run_case(extract, case, email_ids[case["id"]]))
        _print_row(rows[-1])
        time.sleep(PAUSE_SECONDS)

    api_failed = [i for i, row in enumerate(rows) if row["status"] == "api_error"]
    if api_failed:
        print(f"\n{len(api_failed)} API error(s): waiting {API_RETRY_PAUSE_SECONDS}s, then retrying them once")
        time.sleep(API_RETRY_PAUSE_SECONDS)
        for i in api_failed:
            rows[i] = run_case(extract, by_id[rows[i]["id"]], email_ids[rows[i]["id"]])
            _print_row(rows[i])
            time.sleep(PAUSE_SECONDS)

    failed = [row for row in rows if row["status"] != "api_error" and not row["perfect"]]
    if len(failed) > MAX_RECHECK_CASES:
        print(f"\n{len(failed)} failed cases: too many to recheck (max {MAX_RECHECK_CASES}), skipping rechecks")
    elif failed and recheck_runs:
        print(f"\nRechecking {len(failed)} failed case(s), {recheck_runs} more run(s) each")
        for row in failed:
            row["recheck"] = recheck(extract, by_id[row["id"]], email_ids[row["id"]], recheck_runs)
            print(f"  {row['id']:<4} {row['recheck']}")

    m = metrics(rows)
    status, reasons = verdict(m, rows)
    result = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "provider": provider,
        "model": model,
        "dataset": DATASET.name,
        "prompt_sha": hashlib.sha256(system_prompt.encode()).hexdigest()[:10],
        "bar": {"critical_clean": BAR_CRITICAL_CLEAN, "minor_fields": BAR_MINOR_FIELDS},
        "verdict": status,
        "reasons": reasons,
        "metrics": m,
        "cases": rows,
    }
    _save(result)
    _print_report(result, rows)
    return result


def _save(result: dict) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_model = result["model"].replace("/", "_").replace(":", "_")
    run_file = RESULTS_DIR / f"{datetime.now():%Y-%m-%d_%H%M%S}_{safe_model}.json"
    run_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    m = result["metrics"]
    summary = {key: result[key] for key in ("date", "provider", "model", "dataset", "prompt_sha", "verdict")} | {
        key: m[key] for key in ("critical_clean", "scored", "minor_fields_rate", "invented_lines", "api_errors",
                                "critical_clean_per_difficulty", "mean_seconds")
    } | {"file": run_file.name}
    with (RESULTS_DIR / "history.jsonl").open("a", encoding="utf-8") as history:
        history.write(json.dumps(summary, ensure_ascii=False) + "\n")
    result["file"] = str(run_file)


def _print_report(result: dict, rows: list[dict]) -> None:
    m = result["metrics"]
    print(f"\nVERDICT: {result['verdict']}" + "".join(f"\n  - {reason}" for reason in result["reasons"]))
    print(f"Critical-clean cases: {m['critical_clean']}/{m['scored']} ({m['critical_clean_rate']:.0%})"
          f"   per difficulty: {m['critical_clean_per_difficulty']}")
    print(f"Perfect cases: {m['perfect']}/{m['scored']}   minor fields: {m['minor_fields_rate']:.0%}"
          f"   invented lines: {m['invented_lines']}   invalid answers: {m['invalid_answers']}"
          f"   API errors: {m['api_errors']}   mean time: {m['mean_seconds']}s")
    for field, value in m["fields"].items():
        print(f"  {field:<20} {value}")
    for row in rows:
        if row["status"] == "api_error":
            print(f"\n- {row['id']} NOT SCORED (API error): {row['error']}")
        elif not row["perfect"]:
            level = "CRITICAL" if not row["critical_ok"] else "minor"
            again = f", recheck: {row['recheck']}" if "recheck" in row else ""
            print(f"\n- {row['id']} [{level}{again}] ({row['difficulty']}, {row['note']})")
            for problem in row["problems"]:
                print(f"    {problem}")
    print(f"\nSaved: {result['file']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="", help="only run models whose name contains this text")
    parser.add_argument("--recheck", type=int, default=RECHECK_RUNS, help="extra runs per failed case (0 = none)")
    args = parser.parse_args()

    load_dotenv()
    cases = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    system_prompt = build_system_prompt(ONDYNA_PROFILE)
    chain = [(provider, model) for provider, model in PROVIDER_CHAIN if args.model in model]
    if not chain:
        raise SystemExit(f"no model in PROVIDER_CHAIN contains {args.model!r}")

    results = [evaluate_model(provider, model, cases, system_prompt, args.recheck) for provider, model in chain]

    print("\n=== summary ===")
    for r in results:
        m = r["metrics"]
        print(f"{r['verdict']:<10} {r['model']:<42} critical-clean {m['critical_clean']:>2}/{m['scored']}"
              f"  minor {m['minor_fields_rate']:.0%}  invented {m['invented_lines']}  API errors {m['api_errors']}"
              f"  {m['mean_seconds']}s/case")
    passing = sorted((r for r in results if r["verdict"] == "PASS"), key=lambda r: r["metrics"]["mean_seconds"])
    if passing:
        print("\nSuggested PROVIDER_CHAIN order (passing models, fastest first):")
        for r in passing:
            print(f'    ("{r["provider"]}", "{r["model"]}"),')
    others = [r["model"] for r in results if r["verdict"] != "PASS"]
    if others:
        print(f"Not ready for the chain (fix the prompt or remove): {', '.join(others)}")


if __name__ == "__main__":
    main()
