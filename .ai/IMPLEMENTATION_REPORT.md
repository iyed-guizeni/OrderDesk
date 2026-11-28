# Implementation Report

## 1. Summary
- Centralized production graph construction in a side-effect-free `workflow.app` module.
- Added workflow lifecycle persistence and a PostgreSQL-backed LangGraph checkpointer.
- Wired the worker to run and checkpoint email workflows, with review status persisted across processes.
- Added the review service and CLI, plus unit and Postgres integration coverage.

## 2. Files
- `src/orderdesk/workflow/app.py`: creates the production graph and provider chain on demand.
- `src/orderdesk/workflow/studio.py`: delegates graph construction to the shared builder while keeping Studio's checkpointer unset.
- `evals/run_e2e.py`: imports the provider chain from `workflow.app`.
- `evals/run_resolver.py`: imports the provider chain from `workflow.app`.
- `evals/run_extract.py`: also imports the provider chain from `workflow.app`; it was another actual importer of `studio`.
- `src/orderdesk/db/migrations/0005_email_workflows.sql`: creates the workflow lifecycle table and tenant/status index.
- `src/orderdesk/domain/workflow_run.py`: adds the frozen workflow-run model and stable thread-id helper.
- `src/orderdesk/db/email_workflows.py`: adds tenant-scoped persistence and atomic review claiming.
- `src/orderdesk/db/checkpointer.py`: opens and initializes the PostgreSQL LangGraph saver.
- `scripts/migrate.py`: initializes checkpoint tables after application migrations.
- `pyproject.toml`: adds `langgraph-checkpoint-postgres`.
- `src/orderdesk/jobs/worker.py`: adds the graph handler, workflow status transitions, configurable `run_forever` handlers, and metadata-only job logs.
- `scripts/run_workers.py`: builds storage, checkpointer, graph and production handler once at startup.
- `src/orderdesk/services/__init__.py`: creates the application services package.
- `src/orderdesk/services/review.py`: adds pending-draft lookup, atomic review submission, and pure CLI change parsing.
- `scripts/review.py`: adds list/show/approve/reject commands around the review service.
- `tests/unit/test_graph.py`: verifies importing the app module does not load settings.
- `tests/unit/test_review_cli.py`: tests merged edits, removal parsing, and invalid input.
- `tests/integration/test_migrate.py`: verifies migration 0005 is applied.
- `tests/integration/test_email_workflows.py`: tests workflow-row idempotency, tenant filtering, review claims and updates.
- `tests/integration/test_worker_graph.py`: tests worker statuses, retry/redelivery, checkpoint hand-off, review outcomes, and recovery after errors.

## 3. Decisions
- Installed `langgraph-checkpoint-postgres` **3.2.1** and used its `PostgresSaver(conn)` constructor with an autocommit psycopg connection, `dict_row`, and `prepare_threshold=0`.
- Closed saver connections explicitly in the migration, worker and CLI entry points.
- Added row-count checks to workflow status updates so a missing or cross-tenant workflow is surfaced instead of silently ignored.
- The task named two eval scripts that imported `studio`; repository search found a third (`run_extract.py`), so its provider-chain import was moved too.
- The queue has a unique `(email_id, kind)` job constraint. The redelivery integration test calls `enqueue` and resets the existing completed job to pending to simulate redelivery without weakening that constraint.
- `parse_changes` lives in `services/review.py` so it can be imported and tested without executing CLI code. Line indexes remain zero-based, matching the workflow's `line_index`.

## 4. Deviations
- `run_extract.py` was updated in addition to the two eval scripts named in the task because it also imported `PROVIDER_CHAIN` from `studio`.
- To keep the changed eval files Ruff-clean, fixed their import ordering and pre-existing line-length/`zip(strict=False)` warnings; these are small adjacent lint corrections.

## 5. Tests
- `venv\\Scripts\\python.exe scripts\\migrate.py`
  - `applied 0005_email_workflows.sql`
- `venv\\Scripts\\python.exe scripts\\migrate.py` (second run)
  - `database is up to date`
- `venv\\Scripts\\python.exe -m pytest tests/unit -q`
  - `267 passed in 3.60s`
- `venv\\Scripts\\python.exe -m pytest tests/integration -q`
  - `33 passed in 9.21s` (no skips)
- `venv\\Scripts\\python.exe -m ruff check` on all changed Python files
  - `All checks passed!`
- `git diff --check`
  - no whitespace errors
- No `evals/run_*.py` scripts were run.

## 6. Open points
- As specified for this batch, quotations and learned aliases remain in each process's in-memory `MemoryCatalogue`; the review CLI and worker do not share those writes. Batch 4 should move those adapters to Postgres.
- No commit was created.
