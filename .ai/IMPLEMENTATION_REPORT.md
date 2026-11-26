# Implementation Report

## 1. Summary
- Added catalogue prices and normalised client-reference lookup for past confirmed orders.
- Added deterministic draft-order validation with line/order warnings and totals.
- Added the human review interrupt with validated approve/reject decisions and line changes.
- Routed every new-order path through validate and review, and updated the e2e graph/checkpointer setup.

## 2. Files
- `src/orderdesk/domain/catalogue.py`: added `Product.list_price` and `PastOrder.client_ref`.
- `src/orderdesk/catalogue/ports.py`: added `find_order_by_client_ref` to `ErpPort`.
- `src/orderdesk/catalogue/memory.py`: implemented normalised, customer-filtered client-reference lookup.
- `src/orderdesk/profiles/demo_catalogue.py`: added invented ERP prices and demo PO references.
- `src/orderdesk/domain/draft.py`: added draft line, order issue and draft order models.
- `src/orderdesk/checks/order.py`: added draft construction and review decision application logic.
- `src/orderdesk/workflow/validate.py`: added the validate node.
- `src/orderdesk/domain/review.py`: added frozen line-change and review-decision models.
- `src/orderdesk/workflow/review.py`: added the interrupt/revalidate review loop.
- `src/orderdesk/workflow/state.py`: added draft, decision and approved-order state fields.
- `src/orderdesk/workflow/graph.py`: routed resolve/choose/agent paths through validate and review; added optional checkpointer.
- `evals/run_e2e.py`: added `InMemorySaver` and unique thread IDs.
- `tests/unit/test_resolve.py`: added memory catalogue client-reference tests.
- `tests/unit/test_validate.py`: added validate-model and warning tests.
- `tests/unit/test_review.py`: added direct and graph review/resume tests.
- `tests/unit/test_graph.py`: updated graph invokes to use thread IDs and an in-memory checkpointer.

## 3. Decisions
- Used `draft.blocking` as every existing order issue because the batch specifies that every order issue is blocking.
- Kept malformed review answers in a repeated interrupt loop by converting `pydantic.ValidationError` into a payload error.
- Added a small internal line-rebuild helper so validate and review share the exact issue, price and total rules.
- Used `strict=False` for an existing e2e summary `zip` only in the touched run_e2e file, matching unequal-list behaviour.

## 4. Deviations
- The full Ruff command still reports 38 errors in pre-existing files outside this batch; all files touched by this implementation pass Ruff.
- No deviations from the requested models, signatures or graph routing.

## 5. Tests
- `venv\Scripts\python.exe -m pytest tests/unit -q`
- Result: `230 passed in 7.54s`
- `venv\Scripts\python.exe -m ruff check <all batch-touched files>`
- Result: `All checks passed!`
- `venv\Scripts\python.exe -m ruff check src tests evals`
- Result: failed with `38 errors` in pre-existing files outside the batch; no eval scripts were run.

## 6. Open points
- The lead should review the 38 repository-wide Ruff violations before requiring a globally clean lint run.
- Existing evaluation scripts may need separate cleanup; this batch did not run `evals/run_*.py`.
