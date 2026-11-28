# Implementation Report

## 1. Summary
- Added frozen quotation models and separate ERP/alias write protocols.
- Added idempotent in-memory draft quotation writes and alias learn/forget behavior.
- Added write_draft and learn workflow nodes after successful review approval.
- Routed approve through write_draft then learn, while reject skips both steps.

## 2. Files
- `src/orderdesk/domain/quotation.py`: added quotation and alias outcome models plus stable origin generation.
- `src/orderdesk/catalogue/ports.py`: added `ErpWriter` and `AliasWriter`; read and search protocols remain unchanged.
- `src/orderdesk/catalogue/memory.py`: implemented idempotent drafts and alias learn/conflict behavior.
- `src/orderdesk/checks/order.py`: added approved-draft quotation conversion and safe alias-pair extraction.
- `src/orderdesk/resolve/ladder.py`: renamed and published `alias_words` for shared learning and lookup behavior.
- `src/orderdesk/workflow/write_draft.py`: added the approved-draft ERP write node.
- `src/orderdesk/workflow/learn.py`: added the post-write synonym learning node.
- `src/orderdesk/workflow/state.py`: added ERP and alias learning state fields.
- `src/orderdesk/workflow/graph.py`: added approve routing through write_draft and learn.
- `src/orderdesk/workflow/studio.py`: passed the demo catalogue as both writer ports.
- `evals/run_e2e.py`: passed the fresh per-run catalogue as both writer ports.
- `tests/unit/test_memory_catalogue.py`: added origin, idempotency, draft isolation and alias tests.
- `tests/unit/test_write_draft.py`: added quotation conversion, node and idempotency tests.
- `tests/unit/test_learn.py`: added synonym rule, conflict and loop-closing tests.
- `tests/unit/test_graph.py`: updated graph construction with required writer ports.
- `tests/unit/test_review.py`: updated graph construction and added approve/write/reject/loop tests.
- `tests/unit/test_resolver_agent.py`: updated graph construction with required writer ports and a checkpointer/thread.

## 3. Decisions
- `QuotationLine.unit_price` defaults to `None`, since it means that the ERP should choose its own price.
- Draft quotation ids use the `DRAFT-0001` module-prefix pattern; the MemoryCatalogue stores drafts only by origin and never in confirmed orders.
- Alias learning is derived from approved line indexes and the extracted lines, so removed lines and non-named lines are excluded.

## 4. Deviations
- none

## 5. Tests
- `venv\Scripts\python.exe -m pytest tests/unit -q`
- Result: `260 passed in 7.26s`
- `venv\Scripts\python.exe -m ruff check <all batch-2-touched files>`
- Result: `All checks passed!`
- `venv\Scripts\python.exe -m ruff check src tests evals`
- Result: failed with `37 errors` in repository files outside the batch-touched-file lint set.
- No `evals/run_*.py` script was run.

## 6. Open points
- The lead should address the 37 remaining repository-wide Ruff violations separately.
- `ErpWriter` and `AliasWriter` are used only by the graph write and learn nodes; no agent or resolve code references write methods.
