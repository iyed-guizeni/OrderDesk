# REVIEW REPORT: production loop, batch 1 (Validate + Review pause)

**Reviewer:** the lead engineer (Claude Code). **Date:** 2026-11-26.
**Outcome:** ✅ **ACCEPTED, after corrections by the lead** (listed below).

## 1. What I verified myself

I didn't rely on `IMPLEMENTATION_REPORT.md`. I checked it as follows:

- I read the full diff of the 9 changed files and the 7 new files.
- `pytest tests/unit -q`: **230 passed**. This matches Copilot's report.
- I checked Copilot's lint claim. `ruff check` is clean on every touched file. The 38 errors repo-wide are
  **pre-existing**: the last commit, without this batch, has the same set. Copilot fixed one of them.
- `studio.py` is unchanged.
- **Live run:** `python -m evals.run_e2e --only e2e-01,e2e-04,e2e-09` gave experiment
  `e2e-nemotron-3.5-super-vl-120b-a12b-1edf0920`.
  - The graph now pauses at review, and the paused state is still scored.
  - Verdict **PASS**: silent_wrong 0, labels 1.0, PO 1.0.
  - e2e-09 misses `2 x PH6252` again. That's the same miss as the full run, and safe (see §5).

## 2. Implemented correctly

- **Catalogue:**
  - `Product.list_price` and `PastOrder.client_ref`;
  - `ErpPort.find_order_by_client_ref`, a read method compared with `code_key` and filtered by customer;
  - demo prices and POs `BC-2187` and `4302`, which avoid the e2e PO numbers.
- **Validate:**
  - The models are as specified, frozen, and hold `customer_id` only.
  - `build_draft` follows every line and order rule.
  - The graph routes through a path map, so `needs_agent` and `needs_choice` are untouched.
- **Review:**
  - The `interrupt()` loop has nothing with a side effect before the pause.
  - A `ValidationError` becomes an error and the reviewer is asked again.
  - The changed product is the ERP's `Product`, never one built from the answer.
  - The line logic is shared with Validate, not copied.
  - The `checkpointer` parameter is passed to `compile`.
- **`run_e2e.py`:** `InMemorySaver`, plus one `thread_id` per email and repetition.
- **Tests:** the required graph tests are present and meaningful, using pause, resume, reject, change and pause-again.

## 3. Problems found, and corrected by me

| # | Problem | Why it matters | Fix |
|---|---|---|---|
| 1 | `apply_decision` returned at the **first** error. | The reviewer fixed one problem, resubmitted, and only then learned about the next. | It now collects every change error and returns them all. |
| 2 | Blocking order issues (PO already used, unknown customer) were reported only **after** every line was fine. | Someone could fix 3 lines and only then learn the order can't be approved at all. | The blocking issues are now always listed with the line errors. |
| 3 | A **discontinued product already on a line** could be approved. Only a *changed* code was checked. | Odoo can't sell an archived product, so batch 2's ERP write would fail after the approval. | Approval is refused with `line N: product "X" is discontinued: choose another`. |
| 4 | The `unit_differs` condition had a redundant clause (`not (piece and piece)`). | Hard to read. The behaviour was already correct. | Simplified to `unit is not None and unit != product.uom`, with a comment. |
| 5 | The new modules had no docstrings; every other module in the repo has one. | The project style. The "re-run from the first line" rule of `interrupt()` must be visible to the next person. | Added module docstrings, including that warning in `workflow/review.py`. |

## 4. Missing tests, added by me (230 → 238)

`test_validate.py`:
- `test_a_po_of_another_customer_is_not_a_duplicate`: required by task 2.7.
- `test_an_unknown_customer_skips_the_po_check`: task 2.8. The ERP lookup raises if it is called.
- `test_the_draft_holds_no_customer_name`: task 2.10 and the data-minimisation rule.
- `test_the_node_refuses_to_run_before_resolve`: the node's `ValueError`.
- `test_prices_never_reach_the_llm`: an acceptance criterion that had no test. A price of 987.65 is absent from
  `search_catalogue`, `get_product`, `find_past_orders` and the choice prompt.

`test_review.py`:
- `test_every_problem_is_reported_at_once`: fixes 1 and 2.
- `test_a_discontinued_product_already_on_a_line_cannot_be_approved`: fix 3.
- `test_the_second_pause_tells_the_reviewer_what_is_wrong`: task 3.5 and 3.8 wanted the errors **in the payload**.
  Copilot's tests only checked that the graph paused again.

## 5. Notes, not blocking

- **Formatting noise:** Copilot ran the formatter over whole files (`run_e2e.py`, `test_resolve.py`, the demo
  catalogue…). The diff is bigger than the change, but the code is equivalent and follows the project's `ruff
  format` convention. Accepted. For the next tasks: **format only the lines you change.**
- **e2e-09, `PH6252`:** the agent hands the line to a person in both e2e runs so far (the full run and this one). It's safe, never a wrong product,
  but it's now a stable miss rather than noise. It's the next candidate for a prompt adjustment, which would get an
  entry in `evals/prompt_adjustment.md`. It's outside this batch.
- **38 pre-existing lint errors:** worth one cleanup commit before CI adds `ruff check` as a gate (phase 5).

## 6. Final state

- `pytest tests/unit -q`: **238 passed**.
- `ruff check` on the batch files: **all checks passed**.
- Live e2e (3 emails): **PASS**, with the graph pausing at review.
- Every acceptance criterion of `TASKS.md` is met.
