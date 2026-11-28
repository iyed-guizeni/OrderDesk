# REVIEW REPORT: production loop, batch 2 (write the approved draft + learn synonyms)

**Reviewer:** the lead engineer (Claude Code). **Date:** 2026-11-26.
**Outcome:** ✅ **ACCEPTED, after small corrections by the lead** (listed below).

## 1. What I verified myself

- I read the full diff and every new file. The diff is **small this time**: only the changed lines were formatted,
  as asked. The one exception is a harmless re-wrap of `run_agent` in `test_resolver_agent.py`.
- `pytest tests/unit -q`: **260 passed**. This matches the report.
- `ruff check` on every touched file: clean.
- **Write access:** a grep of `agents/`, `resolve/` and `prompts/` for `ErpWriter`, `AliasWriter`,
  `create_draft_quotation` and `learn_alias` finds **nothing**. `ErpPort`, `ProductSearch` and `AliasStore` are
  unchanged.
- **Studio:** `make_graph()` builds, and has the nodes `validate`, `review`, `write_draft` and `learn`.
- **Live run:** `run_e2e --only e2e-01` gave experiment `e2e-nemotron-3.5-super-vl-120b-a12b-5c1aafd2`, **PASS**.
  The graph pauses at review, so nothing is written.

## 2. Implemented correctly

- `orderdesk_origin` gives `"OrderDesk {tenant}-{email}"`, from values that never change.
- `QuotationRequest` holds `customer_id`, codes, quantities and prices only. It refuses an empty order and a
  quantity of 0.
- `MemoryCatalogue`:
  - The draft write is idempotent by origin, with ids `DRAFT-0001`, `DRAFT-0002`, …
  - Drafts are kept apart from confirmed orders.
  - `learn_alias` returns learned, unchanged or forgotten, and writes into the same normalised dict that
    `find_alias` reads.
- `to_quotation_request` fails loudly on an incomplete approved draft.
- `aliases_to_learn` applies every §2.6 rule: `named` only, no `code_as_written`, no removed lines, and the ladder's
  own `alias_words`.
- The graph:
  - only an approve reaches `write_draft`;
  - `learn` runs after the write;
  - a reject writes nothing and learns nothing.
- Logs carry the ids and counts only. No words, names or addresses.

## 3. Problems found, and corrected by me

| # | Problem | Fix |
|---|---|---|
| 1 | The `ErpWriter` and `AliasWriter` docstrings had lost the **contract**: idempotent by origin, draft only, and the three alias outcomes with delete on conflict. The future Odoo and Postgres adapters will be written from these Protocols, so the rules must live there. | Restored the full contract in `catalogue/ports.py`. |
| 2 | The "loop closes" graph test didn't test a correction. The choice step already picked PF60375, and the reviewer "changed" it to PF60375. | The choice step now picks the wrong product, PF60380, and the test asserts that. The reviewer corrects it to PF60375, and the next resolve finds PF60375 by alias. |
| 3 | The `alias_words` docstring said "normalized", which it isn't: the store normalises. | Corrected. |
| 4 | The `memory.py` docstring dropped the line saying the real adapters keep the same methods. | Restored, with the `product_synonyms` table added. |

## 4. Open points for batch 3 (not blocking)

- **A retried `learn` can turn "forgotten" into "learned".** Run 1 deletes a conflicting alias. If the job crashes
  before LangGraph saves that step and the step runs again, there is no alias left, so the reviewer's product is
  learned. The window is small, and the result is still an approved product, but it isn't idempotent. With the
  Postgres `product_synonyms` table (batch 3), record **which email** each learn or forget came from, with a unique
  key, so a re-run of the same email changes nothing.
- **The 37 lint errors** that were there before this batch: one cleanup commit before CI.
- **e2e-09 `PH6252`:** still the next prompt-adjustment candidate, from batch 1.

## 5. Final state

- `pytest tests/unit -q`: **260 passed**.
- `ruff check` on the touched files: **all checks passed**.
- The Studio graph builds. Live e2e (1 email): **PASS**.
- Every acceptance criterion of `TASKS.md` is met.
