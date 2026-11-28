"""List, inspect and submit decisions for workflows waiting on human review."""

import argparse

from orderdesk.config import load_settings
from orderdesk.db.checkpointer import open_checkpointer
from orderdesk.db.connection import connect
from orderdesk.db.email_workflows import list_waiting_review
from orderdesk.profiles.demo_catalogue import make_demo_catalogue
from orderdesk.services.review import parse_changes, pending_draft, submit_review
from orderdesk.storage import LocalStorage
from orderdesk.workflow.app import build_app_graph


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list emails waiting for review")

    show = commands.add_parser("show", help="show a draft and current review errors")
    show.add_argument("email_id", type=int)

    approve = commands.add_parser("approve", help="approve a draft, optionally editing lines")
    approve.add_argument("email_id", type=int)
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--code", action="append", default=[])
    approve.add_argument("--qty", action="append", default=[])
    approve.add_argument("--remove", action="append", default=[])

    reject = commands.add_parser("reject", help="reject a draft")
    reject.add_argument("email_id", type=int)
    reject.add_argument("--reviewer", required=True)
    reject.add_argument("--comment")
    return parser


def _show_draft(graph, conn, tenant_id: int, email_id: int) -> int:
    pending = pending_draft(graph, conn, tenant_id, email_id)
    if pending is None:
        print(f"Email {email_id} is not waiting for review.")
        return 1
    draft, errors = pending
    print(f"Email {email_id} | PO: {draft.po_number or '(none)'} | total: {draft.total:.2f}")
    for line in draft.lines:
        code = line.product.code if line.product is not None else "(unresolved)"
        print(
            f"  line {line.line_index}: {line.raw_text} | {code} | "
            f"qty {line.quantity} {line.unit or ''} | unit price {line.unit_price} | "
            f"line total {line.line_total} | issues {', '.join(line.issues) or 'none'}"
        )
    for issue in draft.order_issues:
        print(f"  order issue: {issue.detail}")
    for error in errors:
        print(f"  review error: {error}")
    return 0


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    changes: list[dict] = []
    if args.command == "approve":
        try:
            changes = parse_changes(args.code, args.qty, args.remove)
        except ValueError as error:
            parser.error(str(error))

    settings = load_settings()
    storage = LocalStorage(settings.storage_dir)
    checkpointer = open_checkpointer(settings.database_url)
    try:
        graph = build_app_graph(storage, make_demo_catalogue(), checkpointer)
        with connect(settings.database_url) as conn:
            if args.command == "list":
                runs = list_waiting_review(conn, settings.tenant_id)
                if not runs:
                    print("No emails are waiting for review.")
                for run in runs:
                    print(f"email {run.email_id} | status {run.status} | since {run.created_at}")
                return 0

            if args.command == "show":
                return _show_draft(graph, conn, settings.tenant_id, args.email_id)

            if args.command == "approve":
                decision = {"action": "approve", "reviewer": args.reviewer, "changes": changes}
            else:
                decision = {"action": "reject", "reviewer": args.reviewer, "comment": args.comment}

            result = submit_review(graph, conn, settings.tenant_id, args.email_id, decision)
            print(result.outcome)
            for error in result.errors:
                print(f"  {error}")
            if result.erp_quotation_id is not None:
                print(f"Draft quotation: {result.erp_quotation_id}")
            return 0 if result.outcome in {"approved", "rejected"} else 1
    finally:
        checkpointer.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
