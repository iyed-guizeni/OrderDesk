# TASKS: production loop, batch 1 (Validate + Review pause)

> **STATUS: READY TO IMPLEMENT. Copilot, your job is to WRITE the code for Tasks 1–3 now.** Nothing exists
> yet: that is expected, you are the one creating it. Do not review, do not ask what to do. Implement, run the
> tests, fix failures, then write `.ai/IMPLEMENTATION_REPORT.md`. The lead reviews afterwards.

**From:** the lead engineer (Claude Code). **For:** GitHub Copilot Agent, the implementation engineer.
**Read first:** `docs/PROJECT_CONTEXT.md` (sections 2.3, 3.3, 6 and 8), then the files listed in each task.
**When done:** write `.ai/IMPLEMENTATION_REPORT.md` (format at the end). **Do not commit.**

## Context

Today the `new_order` branch ends after `resolve_agent`: the resolved lines go nowhere.
This batch adds the next two steps of the workflow:

```
… resolve / choose / resolve_agent ─► validate ─► review (interrupt: a person decides) ─► END
```

- **Validate** (code only, no LLM) turns the resolved lines into a draft order: prices from the ERP, totals, and
  warnings for the reviewer.
- **Review** pauses the graph with LangGraph `interrupt()`. A person answers approve or reject, optionally with
  line changes. Code checks the answer.

Writing to the ERP, the worker, feedback and synonyms are later batches. **Nothing in this batch writes anything.**

## Global rules (apply to every task)

1. **No LLM in this batch.** Validate and Review are plain code.
2. **Prices never reach an LLM.** Do not add the price to any prompt or tool result (`prompts/`,
   `agents/resolver_tools.py`). Only Validate reads it.
3. **Data minimisation:** the draft stores `customer_id`, never the customer name or the sender address.
4. Follow the project style (`docs/PROJECT_CONTEXT.md` §8.2):
   - frozen Pydantic models;
   - type hints;
   - named constants;
   - node factories `make_<name>_node(...)` that return only the fields they change;
   - pure logic outside `workflow/`;
   - no `print` in `src/`;
   - no commented-out code.
5. Line length 120. Run `venv\Scripts\ruff check src tests evals` and fix what it reports in the files you touch.
6. **Never call a real LLM or LangSmith.** Unit tests use `tests/fakes.py` (`FakeLLM`, `FakeStorage`) and
   `MemoryCatalogue`. Do not run `evals/run_*.py`: they spend free-tier quota. The lead runs them.
7. Keep changes small. Don't refactor code this batch doesn't need.

---

## Task 1: prices and client references in the catalogue

**Files:**
- `src/orderdesk/domain/catalogue.py`
- `src/orderdesk/catalogue/ports.py`
- `src/orderdesk/catalogue/memory.py`
- `src/orderdesk/profiles/demo_catalogue.py`

1. `Product`: add `list_price: float | None = None`, with the comment "unit price excl. tax, from the ERP; None =
   no price set".
2. `PastOrder`: add `client_ref: str | None = None`, with the comment "the client's own PO number (Odoo
   `client_order_ref`)".
3. `ErpPort`: add this method:
   ```python
   def find_order_by_client_ref(self, customer_id: int, client_ref: str) -> PastOrder | None:
       """This customer's confirmed order with this PO number, compared with code_key; or None."""
   ```
4. `MemoryCatalogue`: implement it. Compare with `code_key` from `catalogue/text.py`, so `"BC-2187"` matches
   `"bc 2187"`. Search only this customer's orders.
5. Demo catalogue:
   - Give every product an invented `list_price`, with plausible values: basin mixers about 150 to 200, bath-shower
     mixers about 250, shower columns about 400 to 500, hand showers about 40, cartridges about 12 to 15,
     diverter about 60, hose `FLX-150` about 8 per meter.
   - `_family` takes a price for the whole family.
   - Give `SO-2291` the `client_ref` `"BC-2187"` and `SO-2310` the `client_ref` `"4302"`.
   - **Don't use** `4471`, `2290`, `8812` or `5133`: the e2e emails use those numbers, and their expected results
     must not change.

**Tests** (`tests/unit/test_resolve.py`, or a new `tests/unit/test_memory_catalogue.py`):
- `find_order_by_client_ref` finds `SO-2291` from `"bc 2187"` for customer 2.
- It returns None for customer 1 with the same reference (another customer's order).
- It returns None for an unknown reference.

---

## Task 2: the Validate step

**New files:**
- `src/orderdesk/domain/draft.py` (models)
- `src/orderdesk/checks/order.py` (pure logic)
- `src/orderdesk/workflow/validate.py` (node)

**Files to change:**
- `src/orderdesk/workflow/state.py`
- `src/orderdesk/workflow/graph.py`

### Models (`domain/draft.py`)

```python
LineIssue = Literal["no_product", "no_quantity", "no_price", "discontinued", "unit_differs"]
OrderIssueKind = Literal["po_already_used", "unknown_customer", "no_lines"]

class DraftLine(BaseModel):         # frozen
    line_index: int                 # position in ExtractedOrder.lines (-1 is never used)
    raw_text: str                   # the client's line, for the reviewer
    quantity: float | None
    unit: str | None                # as extracted (Unit literal) or None
    product: Product | None
    unit_price: float | None        # product.list_price
    line_total: float | None        # quantity * unit_price, rounded to 2 decimals; None if either is missing
    issues: list[LineIssue] = []
    evidence: str | None = None     # copied from LineResolution.evidence: why this product

class OrderIssue(BaseModel):        # frozen
    kind: OrderIssueKind
    detail: str                     # short, for the reviewer, e.g. "PO 2187 is already on SO-2291"

class DraftOrder(BaseModel):        # frozen
    customer_id: int | None
    po_number: str | None
    lines: list[DraftLine]
    order_issues: list[OrderIssue] = []
    total: float                    # sum of the known line_total values, rounded to 2 decimals

    @property
    def blocking(self) -> list[OrderIssue]: ...   # every order issue is blocking in this batch
```

### Rules (`checks/order.py`)

The signature is `build_draft(extracted: ExtractedOrder, resolved: ResolvedOrder, erp: ErpPort) -> DraftOrder`.
- One `DraftLine` per `LineResolution`, in order. `raw_text`, `quantity` and `unit` come from
  `extracted.lines[resolution.line_index]`.
- Line issues:
  - `no_product`: the resolution has no product.
  - `no_quantity`: quantity is None or `<= 0`.
  - `no_price`: there's a product, but its `list_price` is None.
  - `discontinued`: there's a product with `active=False`.
  - `unit_differs`: the extracted unit is set, differs from `product.uom`, and is not `"piece"` while the uom is
    `"piece"`.
    - So `unit=None` or `"piece"` with uom `"piece"` is fine.
    - `"box"` with uom `"piece"` is an issue.
    - `"meter"` with uom `"meter"` is fine.
- Order issues:
  - `unknown_customer`: `resolved.customer` is None.
  - `po_already_used`: `extracted.po_number` is set, the customer is known, and
    `erp.find_order_by_client_ref(customer.id, po_number)` returns an order. The detail names that order id.
  - `no_lines`: there are no lines at all.
- The total adds up only the lines that have a `line_total`.
- **Validate never changes a product or a quantity.** It only prepares and warns.

### Node (`workflow/validate.py`)

- `make_validate_node(erp: ErpPort)` returns a node that reads `state.extracted_order` and `state.resolved_order`.
- It returns `{"draft_order": build_draft(...)}`.
- If either input is None, it raises `ValueError` (that would be a wiring bug).

### State and graph

- `WorkflowState`: add `draft_order: DraftOrder | None = None`.
- In `graph.py`, every path that ends the new_order branch now goes to `validate`. Do it with a path map: don't
  change `needs_agent` or `needs_choice`, which other code and tests use. For example:
  ```python
  builder.add_conditional_edges("resolve", after_resolve, {"choose": "choose", "resolve_agent": "resolve_agent", END: "validate"})
  builder.add_conditional_edges("choose", needs_agent, {"resolve_agent": "resolve_agent", END: "validate"})
  builder.add_edge("resolve_agent", "validate")
  ```
- `validate` is followed by `review` (Task 3).

**Tests:** create `tests/unit/test_validate.py`. Use a small `MemoryCatalogue` built inside the test file, not the
demo catalogue, so the test shows its own data. Write one idea per test:
1. A resolved line with a quantity gets the price and the line total; the order total is the sum.
2. An unresolved line gets `no_product`, a None price and total, and doesn't count in the total.
3. `no_quantity` for `quantity=None`.
4. `no_price` for a product without `list_price`.
5. `discontinued` for an inactive product.
6. `unit_differs`: `"box"` vs `"piece"` is an issue; `None` vs `"piece"` and `"meter"` vs `"meter"` are not.
7. `po_already_used` when the PO matches a past order of this customer, with the order id in `detail`. There's no
   issue when it matches another customer's order.
8. `unknown_customer` when `resolved.customer` is None. `po_already_used` is then not checked.
9. `no_lines` for an empty order.
10. The draft has no customer name field: `"name" not in DraftOrder.model_fields` and none in its dump.

---

## Task 3: the Review pause (human in the loop)

**New files:**
- `src/orderdesk/domain/review.py`
- `src/orderdesk/workflow/review.py`
- add `apply_decision` to `src/orderdesk/checks/order.py`

**Files to change:**
- `src/orderdesk/workflow/state.py`
- `src/orderdesk/workflow/graph.py`
- `evals/run_e2e.py`
- `tests/unit/test_graph.py`

### Models (`domain/review.py`)

```python
class LineChange(BaseModel):        # frozen, extra="forbid"
    line_index: int
    code: str | None = None         # replace the product with this ERP code
    quantity: float | None = None   # replace the quantity
    remove: bool = False            # drop the line from the order

class ReviewDecision(BaseModel):    # frozen, extra="forbid"
    action: Literal["approve", "reject"]
    reviewer: str                   # who decided (a user id later; any non-empty string now)
    changes: list[LineChange] = []  # only with approve
    comment: str | None = None
```

"Edit" is not a separate action: it means approve with `changes`.

### Rules

The signature is `apply_decision(draft, decision, erp) -> tuple[DraftOrder | None, list[str]]`. It returns the
approved draft (None for reject) and a list of errors. When the list isn't empty, the draft is None.
- `reject`: return `(None, [])`. Changes on a reject are an error: `"a reject takes no line changes"`.
- `approve` checks these, in this order:
  1. Each change's `line_index` must exist in the draft. No index may appear twice.
  2. A `code` must exist (`erp.get_product`) and be active. Store the ERP's own `Product`, never one built from the
     answer.
  3. A `quantity` must be `> 0`.
  4. After the changes:
     - at least one line is left;
     - every remaining line has a product and a quantity;
     - `draft.blocking` is empty.
     
     Otherwise give one clear error per problem, for example `"line 2 has no product: choose one or remove it"`
     or `"PO 2187 is already on SO-2291"`.
- Rebuild the changed lines with new `unit_price`, `line_total` and `issues`, using the same rules as Task 2. A
  changed product sets `evidence` to `"chosen by {reviewer}"`. Recompute the total. Removed lines are dropped.
  **Reuse the Task 2 line logic, don't copy it**: extract a helper such as `draft_line(...)`.

### Node (`workflow/review.py`)

`make_review_node(erp)` returns a node that does this:
```python
def review(state):
    draft = state.draft_order                     # ValueError if None
    errors: list[str] = []
    while True:
        answer = interrupt({"draft": draft.model_dump(mode="json"), "errors": errors})
        decision = ReviewDecision.model_validate(answer)          # a bad shape: errors = [str(...)] and ask again
        approved, errors = apply_decision(draft, decision, erp)
        if not errors:
            return {"review_decision": decision, "approved_order": approved}
```
- **Nothing with side effects goes before `interrupt()`.** LangGraph re-runs the node from its first line on every
  resume. Several `interrupt()` calls in one node are matched to resume values by order, which is how the
  "ask again" loop works. Read the LangGraph docs on `interrupt` (langgraph 1.2.18 is installed).
- A malformed answer, such as a missing field or an unknown action, must not crash the run. Catch
  `pydantic.ValidationError`, turn it into errors and ask again.

### State and graph

- `WorkflowState`: add `review_decision: ReviewDecision | None = None` and `approved_order: DraftOrder | None = None`.
- `graph.py`: `validate` → `review` → END. (Batch 2 will route approve → write_draft.)
- `build_graph(...)` gets a new last parameter, `checkpointer: BaseCheckpointSaver | None = None`, passed to
  `builder.compile(checkpointer=checkpointer)`.
- **Leave `workflow/studio.py` unchanged:** `langgraph dev` brings its own persistence and refuses a custom
  checkpointer.

### `evals/run_e2e.py`

`interrupt()` needs a checkpointer, so the e2e graph would stop working without one:
- `build_eval_graph` passes `checkpointer=InMemorySaver()` (from `langgraph.checkpoint.memory`).
- `make_target` invokes with
  `{"run_name": "orderdesk_workflow", "configurable": {"thread_id": f"e2e-{uuid4()}"}}`. A new thread for each
  email and each repetition.
- The result of `graph.invoke` at the pause still holds the state values, so `facts()` must keep working. Check that
  `"__interrupt__"` in the result doesn't break it.
- The metrics and the case file don't change.

### Tests

Create `tests/unit/test_review.py`, using `InMemorySaver` and a thread id per test:
1. A new_order email runs to the pause. The result has `"__interrupt__"`, the payload holds the draft, and
   `approved_order` is not set yet.
2. Resume with approve and no changes: `approved_order` equals the draft, and `review_decision.reviewer` is kept.
3. Resume with reject: `approved_order` is None.
4. Approve with a change that picks a product for an unresolved line: the line now has that product, its price
   and its total, and the evidence is `"chosen by …"`.
5. Approve while a line has no product: the graph pauses **again**, with an error naming the line. A second resume
   with `remove=True` for that line is then accepted.
6. An unknown code, a discontinued code, quantity 0 and a duplicated `line_index` are each refused, with an error.
   Test `apply_decision` directly for these; no graph is needed.
7. A blocked draft (`po_already_used`) can't be approved, but can be rejected.
8. A malformed answer (`{"action": "maybe"}`) pauses again with an error. It doesn't crash.
9. A label other than new_order never reaches review: no `"__interrupt__"`.

Update `tests/unit/test_graph.py`: new_order tests now stop at the pause, so `make_graph` passes an
`InMemorySaver()`, and each `invoke` gets a `thread_id`. Their assertions stay as they are.

---

## Acceptance criteria (the lead checks each one)

- [ ] `venv\Scripts\python -m pytest tests/unit -q` passes: all 212 earlier tests (updated only where this file
      says) plus the new ones.
- [ ] `venv\Scripts\ruff check src tests evals` is clean on the touched files.
- [ ] No price appears in any prompt or agent tool result. A quick test or a grep proves it.
- [ ] The draft never holds a customer name or a sender address.
- [ ] Nothing writes to the ERP. `ErpPort` gained one read method only.
- [ ] A new_order email always pauses at review. The other labels never do.
- [ ] Wrong decisions are refused with clear errors and asked again. They never crash and are never accepted.
- [ ] `evals/run_e2e.py` still builds its graph and target. The lead runs it live.
- [ ] `studio.py` is unchanged.

## `.ai/IMPLEMENTATION_REPORT.md` format

1. **Summary:** what was built, in 5 lines or fewer.
2. **Files:** each file created or changed, with one line on why.
3. **Decisions:** every place where you chose something this file didn't specify, and why.
4. **Deviations:** anything done differently from this file, and why. Write "none" if there are none.
5. **Tests:** the exact commands run, and the pasted summary lines of the output (pass/fail counts).
6. **Open points:** anything unsure, unfinished or worth the lead's attention.
