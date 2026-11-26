# TASKS: production loop, batch 2 (write the approved draft + learn synonyms)

> **STATUS: READY TO IMPLEMENT. Copilot, your job is to WRITE the code for Tasks 1–4 now.** Nothing exists
> yet: that is expected, you are the one creating it. Do not review, do not ask what to do. Implement, run the
> tests, fix failures, then write `.ai/IMPLEMENTATION_REPORT.md`. The lead reviews afterwards.

**From:** the lead engineer (Claude Code). **For:** GitHub Copilot Agent, the implementation engineer.
**Read first:**
- `docs/PROJECT_CONTEXT.md`: §2.3 steps 6–7, §2.6 Product synonyms, §3.3 Ports, §6 ADR 3, 7, 23 and 29.
- `.ai/archive/batch-1/REVIEW_REPORT.md`: what batch 1 built, and the review's remarks.
- The code of batch 1: `domain/draft.py`, `domain/review.py`, `checks/order.py`, `workflow/review.py` and
  `workflow/graph.py`.

**When done:** write `.ai/IMPLEMENTATION_REPORT.md` (format at the end). **Do not commit.**

## Context

Batch 1 ends at the Review pause: a person approves or rejects the draft. This batch adds what happens after an
**approve**:

```
… validate ─► review ─► approve ─► write_draft ─► learn ─► END
                      └─ reject ──────────────────────────► END   (nothing written, nothing learned)
```

- **write_draft** creates a **draft quotation** in the ERP. It is never a confirmed order (ADR 3).
  - It is **idempotent**. The quotation carries the OrderDesk ID in `origin`, built from the tenant and the email:
    `"OrderDesk 999-42"`.
  - A retried job that runs the step twice gets the same quotation back, never a second one.
- **learn** remembers the client's words for each approved product, so the next email with the same words resolves
  at the alias step, with no LLM. These are the "product synonyms" of §2.6.

The Postgres checkpointer, the worker, the review script and LangSmith feedback are batch 3. Don't build them.

## Global rules (apply to every task)

1. **No LLM in this batch.** Every new step is plain code.
2. **The model never gets write access.** The write methods live on **new, separate Protocols**: `ErpWriter` and
   `AliasWriter`. Only the `write_draft` and `learn` nodes receive them. Never add a write method to `ErpPort`,
   `ProductSearch` or `AliasStore`: those reach the agent's tools and the resolve ladder.
3. **Data minimisation:**
   - The quotation request holds `customer_id`, codes, quantities and prices only.
   - Logs use `tenant_id` and `email_id`, never names, addresses or email text.
4. **Style** (`docs/PROJECT_CONTEXT.md` §8.2):
   - frozen Pydantic models;
   - type hints;
   - named constants;
   - node factories `make_<name>_node(...)` that return only the fields they change;
   - pure logic outside `workflow/`;
   - a module docstring on every new file;
   - no `print` in `src/`;
   - no commented-out code.
5. **Format only the lines you change.** Do **not** run `ruff format` on whole files: in batch 1, that turned small
   changes into large diffs. Run `venv\Scripts\ruff check <the files you touched>` and fix what it reports.
6. **Never call a real LLM or LangSmith.** Unit tests use `tests/fakes.py` and `MemoryCatalogue`. Do not run
   `evals/run_*.py`: the lead runs them.
7. Keep changes small. Don't refactor code this batch doesn't need.

---

## Task 1: the write side of the ERP (ports + memory adapter)

**Files:**
- new `src/orderdesk/domain/quotation.py`
- `src/orderdesk/catalogue/ports.py`
- `src/orderdesk/catalogue/memory.py`

### Models (`domain/quotation.py`)

```python
ORIGIN_PREFIX = "OrderDesk"

def orderdesk_origin(tenant_id: int, email_id: int) -> str:
    """'OrderDesk 999-42': the same email always gives the same origin. That is what makes the write idempotent."""

class QuotationLine(BaseModel):     # frozen, extra="forbid"
    code: str
    quantity: float                 # > 0 (Field(gt=0))
    unit_price: float | None        # the ERP price shown to the reviewer; None = let the ERP price it

class QuotationRequest(BaseModel):  # frozen, extra="forbid"
    origin: str                     # orderdesk_origin(...)
    customer_id: int
    client_ref: str | None          # the client's PO number (Odoo client_order_ref)
    lines: list[QuotationLine]      # min_length=1
```

### Ports (`catalogue/ports.py`)

```python
class ErpWriter(Protocol):
    """The ONLY way OrderDesk writes to the ERP. Given to the write_draft node alone, never to an agent."""

    def create_draft_quotation(self, request: QuotationRequest) -> str:
        """Create a DRAFT quotation and return its ERP id. Idempotent: if a quotation with this origin already
        exists, return its id and create nothing."""
        ...

class AliasWriter(Protocol):
    """Remembers a person's approval. Given to the learn node alone."""

    def learn_alias(self, customer_id: int, text: str, code: str) -> None:
        """This customer's words now mean this product. The text is normalised like find_alias's.
        The latest approval wins."""
        ...
```

### Memory adapter (`catalogue/memory.py`)

`MemoryCatalogue` implements both Protocols:
- `create_draft_quotation`:
  - stores the request in a dict keyed by `origin`;
  - ids are `"DRAFT-0001"`, `"DRAFT-0002"`, … (a module constant for the prefix);
  - the same origin returns the same id.
- A read-only property `draft_quotations -> dict[str, QuotationRequest]` (id → request), for tests and the demo.
- **Drafts never appear in `past_orders` or `get_order`.** Those return CONFIRMED orders only (see the `PastOrder`
  docstring). "Same as last time" must never copy a draft.
- `learn_alias` writes into the same alias dict that `find_alias` reads, with the same `normalize` key, so a learned
  alias is found by the next resolve.
- Update the module docstring: it now also implements `ErpWriter` and `AliasWriter`.

**Tests** (new `tests/unit/test_memory_catalogue.py`):
1. `orderdesk_origin(999, 42) == "OrderDesk 999-42"`.
2. Creating the same request twice returns the same id, and `draft_quotations` holds one entry.
3. Two different origins give two ids.
4. A draft never shows up in `past_orders` or `get_order`.
5. After `learn_alias(2, "Robinet  Profilo NOIR", "PF60376")`, `find_alias(2, "robinet profilo noir")` returns
   `"PF60376"`. Another customer gets None.
6. The latest approval wins: learning the same words with another code replaces the first one.
7. `QuotationRequest` refuses an empty line list, and `QuotationLine` refuses `quantity=0`.

---

## Task 2: the write_draft node

**Files:**
- new `src/orderdesk/workflow/write_draft.py`
- add `to_quotation_request` to `src/orderdesk/checks/order.py`
- `src/orderdesk/workflow/state.py`

- **Pure function** in `checks/order.py`:
  `to_quotation_request(approved: DraftOrder, tenant_id: int, email_id: int) -> QuotationRequest`.
  - `origin` comes from `orderdesk_origin(tenant_id, email_id)`.
  - `client_ref` is the draft's `po_number`.
  - Lines are in order: `code`, `quantity` and `unit_price` from each approved `DraftLine`.
  - Raise `ValueError` if `customer_id` is None, or if a line has no product or no quantity. Review already refuses
    these, so reaching this is a bug, and it must fail loudly, not write half an order.
- **Node:** `make_write_draft_node(writer: ErpWriter)` returns a node that:
  - raises `ValueError` if `state.approved_order` is None;
  - builds the request from `state.email.tenant_id` and `state.email.email_id`;
  - calls `writer.create_draft_quotation`;
  - returns `{"erp_quotation_id": quotation_id}`;
  - logs one line: `logging.getLogger(__name__).info("draft quotation %s for tenant %s email %s", ...)`.
- `WorkflowState`: add `erp_quotation_id: str | None = None`.

**Tests** (new `tests/unit/test_write_draft.py`):
1. `to_quotation_request` keeps codes, quantities, prices, the PO and the origin, in order.
2. It raises for a draft with no customer, and for a line without a product.
3. The node writes exactly one quotation and returns its id.
4. **Idempotency:** running the node twice on the same state gives the same id, and one quotation in
   `draft_quotations`.
5. The node raises when there is no `approved_order`.

---

## Task 3: the learn node (product synonyms)

**Files:**
- new `src/orderdesk/workflow/learn.py`
- add `aliases_to_learn` to `src/orderdesk/checks/order.py`
- `src/orderdesk/resolve/ladder.py`
- `src/orderdesk/workflow/state.py`

The learning key must be **exactly** what the ladder looks up. In `resolve/ladder.py`, rename the private
`_words(line)` to a public `alias_words(line)` with a one-line docstring, and update its callers in that file. Learn
must reuse it. Don't copy it.

The rules, from §2.6 and ADR 29:
- **Pure function** in `checks/order.py`:
  `aliases_to_learn(approved: DraftOrder, extracted: ExtractedOrder) -> list[tuple[str, str]]`, a list of
  `(words, code)`. A line is learned only when **all** of these hold:
  1. It is in the approved order (a removed line is never learned).
  2. Its extracted line has `product.kind == "named"`: never `previous_order` ("same as last time" carries no
     wording), and never `missing`.
  3. Its extracted line has **no** `code_as_written`: we never learn from codes or references.
  4. `alias_words(extracted_line)` is not empty.
- **Node:** `make_learn_node(aliases: AliasWriter)` returns a node that:
  - raises `ValueError` without `approved_order` or `extracted_order`;
  - does nothing when `approved_order.customer_id` is None;
  - calls `learn_alias(customer_id, words, code)` for each pair;
  - returns `{"learned_aliases": len(pairs)}`.
- `WorkflowState`: add `learned_aliases: int = 0`.
- A line the reviewer **corrected** is learned with the reviewer's product. That is the most valuable case: the
  next email resolves correctly with no LLM.

**Tests** (new `tests/unit/test_learn.py`):
1. A `named` line with words is learned as `(alias_words(line), code)`.
2. `previous_order`, `missing` and code-only lines (`code_as_written` set) are not learned.
3. A line removed at review is not learned.
4. A corrected line is learned with the **reviewer's** product.
5. **The loop closes:** after the learn node runs on a `MemoryCatalogue`, `resolve_line` on the same words for the
   same customer resolves with `method="alias"`.
6. An unknown customer (`customer_id=None`) learns nothing and doesn't fail.

---

## Task 4: wire it into the graph

**Files:**
- `src/orderdesk/workflow/graph.py`
- `src/orderdesk/workflow/studio.py`
- `evals/run_e2e.py`
- `tests/unit/test_graph.py`
- `tests/unit/test_review.py`

1. Add `after_review(state) -> Literal["write_draft", "__end__"]` to `graph.py`. It returns `"write_draft"` when
   `state.approved_order` is set, and END otherwise.
2. Change the edges: review goes through `after_review`, then `write_draft` → `learn` → END. Learn runs
   **after** the write: if the write fails, nothing is learned.
3. `build_graph(...)` gets two new **keyword** parameters, `writer: ErpWriter` and `alias_writer: AliasWriter`,
   placed after `aliases` and before `checkpointer`. They are required, with no default: a graph that can approve
   must be able to write.
4. Update every caller:
   - `studio.py`: pass the demo `catalogue` for both. This is the one change allowed in `studio.py`.
   - `evals/run_e2e.py`: pass the fresh per-run `catalogue` for both. The e2e run stops at review, so nothing is
     written. Don't change anything else there.
   - `tests/unit/test_graph.py` and `tests/unit/test_review.py`: pass the test catalogue.
5. Graph tests (add them to `tests/unit/test_review.py`):
   1. Approve → the catalogue holds exactly one draft quotation, whose origin is `"OrderDesk 999-7"` (the
      `make_email()` defaults). The result has `erp_quotation_id`.
   2. Reject → `draft_quotations` is empty and `learned_aliases` is 0.
   3. **Nothing is written before the approval:** at the pause, `draft_quotations` is empty.
   4. Approve with a correction on a `named` line → the next resolve of the same words for that customer gives
      `method="alias"` with the corrected product. This is the loop, from start to end.

---

## Acceptance criteria (the lead checks each one)

- [ ] `venv\Scripts\python -m pytest tests/unit -q` passes: the 238 earlier tests (updated only where this file
      says) plus the new ones.
- [ ] `venv\Scripts\ruff check` is clean on every touched file, and the diff has **no reformatting of untouched
      lines**.
- [ ] `ErpPort`, `ProductSearch` and `AliasStore` are unchanged. The writes live only on `ErpWriter` and
      `AliasWriter`, and those only reach `write_draft` and `learn`. A grep of `agents/` and `resolve/` finds
      neither.
- [ ] Nothing is written before an approve, and nothing at all after a reject.
- [ ] The same email approved twice, or a retried node, gives one quotation.
- [ ] Learning follows the §2.6 rules exactly (`named` only, no codes, no removed lines), and it uses the ladder's
      own `alias_words`.
- [ ] Drafts never appear in `past_orders` or `get_order`.
- [ ] `studio.py` changes only by the two new arguments. `run_e2e.py` changes only by the two new arguments.

## `.ai/IMPLEMENTATION_REPORT.md` format

1. **Summary:** what was built, in 5 lines or fewer.
2. **Files:** each file created or changed, with one line on why.
3. **Decisions:** every place where you chose something this file didn't specify, and why.
4. **Deviations:** anything done differently from this file, and why. Write "none" if there are none.
5. **Tests:** the exact commands run, and the pasted summary lines of the output (pass/fail counts).
6. **Open points:** anything unsure, unfinished or worth the lead's attention.

---

## Later batches (for context only, do not build)

- **Batch 3:**
  - the Postgres checkpointer, with `thread_id = "tenant-{id}:email-{id}"`;
  - the worker runs the graph and marks the email `waiting_review` (migration `0005`);
  - `scripts/review.py`, to list the emails waiting for review and approve, edit or reject one;
  - saving the first run's trace id.
- **Batch 4:**
  - LangSmith feedback on that trace: "approved unchanged" or "corrected" per line, which gives the production
    correction rate;
  - corrected lines become new e2e eval cases.
