# TASKS: production loop, batch 3 (the worker runs the graph, a person reviews from a script)

> **STATUS: READY TO IMPLEMENT. Copilot, your job is to WRITE the code for Tasks 1–5 now.** Nothing exists
> yet: that is expected, you are the one creating it. Do not review, do not ask what to do. Implement, run the
> tests, fix failures, then write `.ai/IMPLEMENTATION_REPORT.md`. The lead reviews afterwards.

**From:** the lead engineer (Claude Code). **For:** GitHub Copilot Agent, the implementation engineer.
**Read first:**
- `docs/PROJECT_CONTEXT.md`: §2.1, §2.2, §3.2, §3.3 "Where things are built", §3.4 error layers, §7.4.
- `.ai/archive/batch-2/REVIEW_REPORT.md`.
- `jobs/queue.py`, `jobs/worker.py`, `intake/repository.py`, `db/connection.py`, `db/migrate.py`,
  `scripts/migrate.py`, `scripts/run_workers.py`, `workflow/studio.py`, `workflow/graph.py`,
  `tests/integration/conftest.py`, `tests/integration/test_jobs.py`.

**When done:** write `.ai/IMPLEMENTATION_REPORT.md` (format at the end). **Do not commit.**

**The database is needed.** Docker Desktop must be running with `docker compose up -d app-db` (port 5434). Then:
`venv\Scripts\python scripts\migrate.py`, then `venv\Scripts\python -m pytest tests/integration -q`. Today it
reports 20 passed. If the database is unreachable, the integration tests **skip**. A skip is not a pass: say so in
the report.

## Context

The graph is complete up to the ERP write, but only Studio and the e2e eval run it. In production, the **worker**
runs it, and a person reviews **later, from another process**. So the graph's state must be saved in Postgres
between the two.

```
intake ─► jobs ─► worker: graph.invoke(...) ─► pauses at review ─► email_workflows.status = waiting_review
                                                                          │   (hours later, another process)
scripts/review.py approve 42 ─► graph.invoke(Command(resume=...)) ─► write_draft ─► learn ─► status = approved
```

This batch builds:
1. the **Postgres checkpointer**;
2. one **`email_workflows`** row per email (its status and the trace id of its first run);
3. **the worker** running the graph;
4. **a review service plus a thin CLI**.

The API (phase 3) will call the same review service.

**Known limit, accepted for this batch:** the ERP and the synonyms are still the in-memory demo catalogue. The
worker and `review.py` are two processes with two catalogues, so a draft written by `review.py` and a learned alias
are lost when it exits. `email_workflows.erp_quotation_id` still records the id. **Batch 4** moves drafts and
synonyms to Postgres. Do not build that now.

## Global rules (apply to every task)

1. **No LLM in tests.** Use `tests/fakes.py` (`FakeLLM`, `FakeStorage`) and `MemoryCatalogue`.
   - Never call a real LLM or LangSmith.
   - Don't run `evals/run_*.py`.
   - Don't run `scripts/run_workers.py` against real providers.
2. **SQL lives in repository modules only.** No SQL in `workflow/`, `services/` or `scripts/`. Every query filters
   by `tenant_id`.
3. **Never edit an applied migration** (0001–0004). Add `0005_email_workflows.sql`.
4. **Build once at the entry point** (ADR 28): the worker builds the graph and the checkpointer **once at start**,
   never per job.
5. **Data minimisation:**
   - Logs carry `tenant_id`, `email_id` and the status, never the sender, the subject or the body.
   - LangSmith metadata: `tenant_id` and `email_id` only.
6. **Style** (§8.2):
   - frozen models;
   - type hints;
   - named constants;
   - module docstrings;
   - no `print` in `src/` (scripts may print);
   - no commented-out code.
7. **Format only the lines you change.** No whole-file `ruff format`. Run `venv\Scripts\ruff check` on the touched
   files.
8. Keep changes small. Don't refactor what this batch doesn't need, except what Task 1 asks.

---

## Task 1: one place that builds the production graph

**Files:**
- new `src/orderdesk/workflow/app.py`
- `src/orderdesk/workflow/studio.py`
- `evals/run_e2e.py`
- `evals/run_resolver.py`

The problem: `studio.py` builds a whole graph **at import**, and both eval scripts import `PROVIDER_CHAIN` from
it. Any import of `studio` therefore loads settings and builds LLM clients. The worker needs the same graph with a
checkpointer.

1. Create `workflow/app.py` with:
   - `PROVIDER_CHAIN`, moved from `studio.py` and unchanged;
   - `build_app_graph(storage: Storage, catalogue: MemoryCatalogue, checkpointer: BaseCheckpointSaver | None) ->
     CompiledStateGraph`. It builds the four LLMs from `PROVIDER_CHAIN`, exactly as `studio.py` does today, and
     calls `build_graph(...)`, passing `catalogue` as `erp`, `search`, `aliases`, `writer` and `alias_writer`.
   - **Nothing at import time:** no `load_settings()` and no graph built.
2. `studio.py`:
   ```python
   GRAPH = build_app_graph(LocalStorage(settings.storage_dir), make_demo_catalogue(), checkpointer=None)
   ```
   It keeps `make_graph()`. Studio brings its own persistence, so the checkpointer must stay `None` here.
3. `evals/run_e2e.py` and `evals/run_resolver.py` import `PROVIDER_CHAIN` from `orderdesk.workflow.app`. Change
   nothing else in them.

**Test:** in `tests/unit/test_graph.py`, add a test that `import orderdesk.workflow.app` doesn't call
`load_settings`: patch it to raise, then import the module fresh with `importlib.reload`.

---

## Task 2: migration 0005 and the `email_workflows` repository

**Files:**
- new `src/orderdesk/db/migrations/0005_email_workflows.sql`
- new `src/orderdesk/db/email_workflows.py`
- new `src/orderdesk/domain/workflow_run.py`

```sql
-- One row per email that entered the workflow: where it stands, and the trace of its first run.
CREATE TABLE IF NOT EXISTS email_workflows (
    email_id          BIGINT       PRIMARY KEY REFERENCES emails (id) ON DELETE CASCADE,
    tenant_id         INT          NOT NULL,
    thread_id         TEXT         NOT NULL UNIQUE,        -- 'tenant-{tenant_id}:email-{email_id}' (checkpointer)
    status            TEXT         NOT NULL CHECK (status IN
                        ('running', 'waiting_review', 'reviewing', 'approved', 'rejected', 'finished')),
    trace_id          UUID,                                -- LangSmith run id of the run that paused at review
    erp_quotation_id  TEXT,                                -- set when approved
    reviewer          TEXT,                                -- who approved or rejected
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS email_workflows_tenant_status_idx ON email_workflows (tenant_id, status);
```

What each status means:
- `running`: the worker is running the graph.
- `waiting_review`: paused at review.
- `reviewing`: a reviewer's answer is being applied.
- `approved` or `rejected`: the reviewer decided.
- `finished`: the email ended without review (a label other than new_order).

The model in `domain/workflow_run.py` is a frozen `WorkflowRun` with the same fields, plus a `Status` Literal and
`thread_id_for(tenant_id, email_id) -> str`.

The repository (`db/email_workflows.py`) has one function per need. Every query filters by `tenant_id`:
- `start(conn, tenant_id, email_id) -> WorkflowRun`:
  - `INSERT … ON CONFLICT (email_id) DO NOTHING` with status `running`, then `SELECT`;
  - it returns the existing row when there is one, so a retried job sees what is already there.
- `mark_waiting_review(conn, tenant_id, email_id, trace_id: UUID) -> None`.
- `mark_finished(conn, tenant_id, email_id) -> None`.
- `claim_for_review(conn, tenant_id, email_id) -> WorkflowRun | None`:
  - `UPDATE … SET status='reviewing' WHERE … AND status='waiting_review' RETURNING …`;
  - this is **atomic**: of two reviewers answering at once, only one gets the row; the other gets None.
- `finish_review(conn, tenant_id, email_id, status: Literal["approved", "rejected", "waiting_review"],
  reviewer: str, erp_quotation_id: str | None) -> None`.
  - `waiting_review` means the answer was refused and the graph paused again.
- `list_waiting_review(conn, tenant_id) -> list[WorkflowRun]`, oldest first.
- `get(conn, tenant_id, email_id) -> WorkflowRun | None`.

Every update also sets `updated_at = now()`.

**Tests** (new `tests/integration/test_email_workflows.py`, marked `integration`; the conftest cleanup already
deletes emails, which cascades):
1. Calling `start` twice gives one row and the same `thread_id`.
2. `claim_for_review` succeeds once. A second call returns None.
3. `list_waiting_review` returns only this tenant's `waiting_review` rows: an `OTHER_TEST_TENANT` row is not listed.
4. `tests/integration/test_migrate.py` still passes, so 0005 applies on top of 0004.

---

## Task 3: the Postgres checkpointer

**Files:**
- new `src/orderdesk/db/checkpointer.py`
- `scripts/migrate.py`
- `pyproject.toml`

1. Install `langgraph-checkpoint-postgres` into the venv and add it to `pyproject.toml` `dependencies`. Write down
   the installed version in the report.
2. `db/checkpointer.py`:
   - `open_checkpointer(database_url: str) -> PostgresSaver` opens its **own** connection, with
     `psycopg.connect(database_url, autocommit=True, prepare_threshold=0, row_factory=dict_row)`. PostgresSaver
     needs `dict_row`, which our `connect()` doesn't set, so don't reuse it.
   - `setup_checkpointer(database_url: str) -> None` calls `saver.setup()`. It is idempotent and creates the
     LangGraph tables.
   - Read the `langgraph-checkpoint-postgres` docs for the exact constructor.
3. `scripts/migrate.py`: after our own migrations, call `setup_checkpointer(...)`. Every schema change then goes
   through one command.

**Test** (integration): save and load a checkpoint round-trip with the real graph. Build a graph with `FakeLLM`s
and a `MemoryCatalogue` that pauses at review, with `open_checkpointer`. Then build a **second** graph object with a
**new** checkpointer connection, which acts like the other process, and resume it with `Command(resume={"action":
"approve", …})` on the same `thread_id`. Assert that `approved_order` is set and the draft survived intact:
`DraftOrder` equality, including dates and floats. This test proves the hand-over between the worker and the
reviewer.

---

## Task 4: the worker runs the graph

**Files:**
- `src/orderdesk/jobs/worker.py`
- `scripts/run_workers.py`

1. `worker.py`: add `make_process_email(graph: CompiledStateGraph) -> Handler`. The handler does this:
   ```python
   run = email_workflows.start(conn, job.tenant_id, job.email_id)
   if run.status != "running":
       return                         # already paused, reviewed or finished: a re-delivered job changes nothing
   email = load_email_context(conn, job.tenant_id, job.email_id)
   run_id = uuid4()
   config = {"configurable": {"thread_id": run.thread_id}, "run_id": run_id, "run_name": "orderdesk_workflow",
             "metadata": {"tenant_id": job.tenant_id, "email_id": job.email_id}}
   result = graph.invoke(WorkflowState(email=email), config)
   if "__interrupt__" in result:  mark_waiting_review(conn, ..., trace_id=run_id)
   else:                          mark_finished(conn, ...)
   ```
   - Any exception propagates. `run_one` already turns it into `mark_failed`, with a backoff retry.
   - A retried job finds status `running` and runs the graph again from the start with the same input. That's
     simple and correct: nothing was written, since writing only happens after review. Write a comment saying so.
2. `run_forever(...)` gets a `handlers: dict[str, Handler] = HANDLERS` parameter, like `run_one` and
   `run_until_empty` already have.
3. **Keep** the existing logging-only `process_email` and `HANDLERS`: the job tests use them. The production
   handler is passed in by the script.
4. `scripts/run_workers.py`, at start and once:
   ```python
   storage = LocalStorage(settings.storage_dir)
   checkpointer = open_checkpointer(settings.database_url)
   graph = build_app_graph(storage, make_demo_catalogue(), checkpointer)
   handlers = {**HANDLERS, PROCESS_EMAIL: make_process_email(graph)}
   ```
   Pass `handlers` to `run_forever` and `run_until_empty`. Load `.env` the way `load_settings` already does.

**Tests** (integration, in `tests/integration/test_worker_graph.py`). Use a graph built by `build_graph` with fakes
and `open_checkpointer`. Never `build_app_graph`, which builds real LLM clients.
1. Ingest a fixture email, then run `run_until_empty` with the graph handler:
   - an email classified `new_order` ends with `email_workflows.status == "waiting_review"` and a `trace_id`;
   - the job is `done`.
2. An email classified `other` ends `finished`, with no trace id needed.
3. **A re-delivered job:** enqueue the same email again after it's waiting for review, and run it. The FakeLLMs are
   **not called again**, and the status stays `waiting_review`.
4. A graph that raises gives a `failed` or retried job, and the status stays `running`.

The sender of the fixture emails must be a known customer of the test catalogue, so that the line resolves. Build
the test `MemoryCatalogue` with that sender's address. Read it from the fixture, and **don't print it** in test
output or logs.

---

## Task 5: the review service and the review CLI

**Files:**
- new `src/orderdesk/services/__init__.py`
- new `src/orderdesk/services/review.py`
- new `scripts/review.py`

### Service (`services/review.py`): the API will reuse it in phase 3

```python
class ReviewResult(BaseModel):          # frozen
    outcome: Literal["approved", "rejected", "asked_again", "not_waiting"]
    errors: list[str] = []              # with asked_again: why the answer was refused
    erp_quotation_id: str | None = None

def pending_draft(graph, conn, tenant_id: int, email_id: int) -> tuple[DraftOrder, list[str]] | None:
    """The draft and the errors of the last refused answer, read from the checkpoint; None if not waiting."""

def submit_review(graph, conn, tenant_id: int, email_id: int, decision: dict) -> ReviewResult:
    run = email_workflows.claim_for_review(conn, tenant_id, email_id)
    if run is None: return ReviewResult(outcome="not_waiting")
    result = graph.invoke(Command(resume=decision), {"configurable": {"thread_id": run.thread_id}})
    if "__interrupt__" in result:   finish_review(..., "waiting_review", ...)  -> asked_again + its errors
    elif approved_order is set:     finish_review(..., "approved", reviewer, erp_quotation_id) -> approved
    else:                           finish_review(..., "rejected", reviewer, None) -> rejected
```

- If `graph.invoke` raises, put the status back to `waiting_review` (use try/except, then **re-raise**). A crash
  must never leave an email stuck in `reviewing`.
- `pending_draft` reads `graph.get_state(config)`: the interrupt's value holds `{"draft": …, "errors": …}`. It
  returns None when the run isn't `waiting_review`.
- `decision` stays a plain dict: the review node validates it, which batch 1 built. Don't validate it twice.
- The reviewer name for `finish_review` comes from `decision.get("reviewer")`. When the shape is invalid it may be
  missing, and that is fine for `asked_again`.

### CLI (`scripts/review.py`): thin, with no logic beyond parsing and printing

```
python scripts/review.py list                                   # emails waiting for review (this TENANT_ID)
python scripts/review.py show 42                                # the draft: lines, prices, issues, errors
python scripts/review.py approve 42 --reviewer amine [--code 1=PF60376] [--qty 1=3] [--remove 2]
python scripts/review.py reject 42 --reviewer amine [--comment "duplicate"]
```

- Put the parsing of `--code 1=PF60376`, `--qty 1=3` and `--remove 2` into a pure function,
  `parse_changes(codes, qtys, removes) -> list[dict]`, so it can be tested. A bad value (`--qty 1=abc`, `--code
  x=Y`) exits with a clear message. It never sends a half-built decision.
- The script builds the graph once, with `build_app_graph(..., open_checkpointer(...))`. The resume runs
  `write_draft` and `learn`, which need no LLM: the graph builds LLM clients but never calls them on resume.
- `list` and `show` may print the subject and the line texts: it's a local terminal for the reviewer, never sent
  anywhere.
- Exit codes: 0 when approved, rejected or shown; 1 when asked again or not waiting; 2 for bad arguments.

**Tests:**
- Unit (new `tests/unit/test_review_cli.py`):
  - `parse_changes` merges `--code 1=X` and `--qty 1=3` on the same line into one change;
  - `--remove` gives `{"line_index": 2, "remove": True}`;
  - bad values raise.
  Import the function without running the CLI. Put `parse_changes` in `services/review.py` if that makes it easier
  to import, and say so in the report.
- Integration (add to `tests/integration/test_worker_graph.py`), with a graph made of fakes and the checkpointer:
  1. the worker pauses an email, then `submit_review(approve)` gives `approved`, the row is `approved` with an
     `erp_quotation_id`, and the job and checkpoint survive between two graph objects;
  2. a reject gives `rejected` and no quotation id;
  3. an approve while a line has no product gives `asked_again`, its errors, and the status back to
     `waiting_review`; a second `submit_review` with `remove` gives `approved`;
  4. `submit_review` on an email that isn't waiting gives `not_waiting`, and the graph is not invoked;
  5. a graph whose resume raises puts the status back to `waiting_review`, and the error propagates.

---

## Acceptance criteria (the lead checks each one)

- [ ] `venv\Scripts\python -m pytest tests/unit -q` passes: the 260 earlier tests plus the new ones.
- [ ] `venv\Scripts\python -m pytest tests/integration -q` passes **with the database up**: the 20 earlier tests
      plus the new ones. No skips.
- [ ] `scripts/migrate.py` applies 0005 and the LangGraph tables, and a second run changes nothing.
- [ ] `ruff check` is clean on the touched files, with no reformatting of untouched lines.
- [ ] Importing `orderdesk.workflow.app` loads no settings and builds nothing. `studio.py` keeps working.
- [ ] The worker builds the graph and checkpointer once. A re-delivered job never re-runs an email that is past
      `running`.
- [ ] A review given in another process (a new graph object, a new connection) resumes the saved thread correctly.
- [ ] Two reviewers answering at once: only one is applied. A crash during the resume never leaves `reviewing`.
- [ ] No SQL outside `db/` repository modules. Every query filters by `tenant_id`.
- [ ] No sender, subject or body in logs or LangSmith metadata.

## `.ai/IMPLEMENTATION_REPORT.md` format

1. **Summary:** what was built, in 5 lines or fewer.
2. **Files:** each file created or changed, with one line on why.
3. **Decisions:** every place where you chose something this file didn't specify, and why.
4. **Deviations:** anything done differently from this file, and why. Write "none" if there are none.
5. **Tests:** the exact commands run, and the pasted summary lines of the output (pass/fail/**skip** counts).
6. **Open points:** anything unsure, unfinished or worth the lead's attention.

---

## Later batches (for context only, do not build)

- **Batch 4:** drafts and synonyms in Postgres.
  - `demo_quotations` (an `ErpWriter`);
  - `product_synonyms` (an `AliasStore` and `AliasWriter`), recording which email each learn or forget came from,
    so that re-running the same email changes nothing (the batch-2 open point);
  - with these, `review.py` and the worker share what they write.
- **Batch 5:** LangSmith feedback on the saved `trace_id`, "approved unchanged" or "corrected" per line, and
  corrected lines become e2e eval cases.
