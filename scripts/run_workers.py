"""Start the production worker.

    python scripts/run_workers.py          # runs until Ctrl+C
    python scripts/run_workers.py --once   # processes waiting jobs, then exits
"""

import argparse
import logging

from orderdesk.config import load_settings
from orderdesk.db.checkpointer import open_checkpointer
from orderdesk.db.connection import connect
from orderdesk.jobs.queue import PROCESS_EMAIL
from orderdesk.jobs.worker import HANDLERS, default_worker_name, make_process_email, run_forever, run_until_empty
from orderdesk.profiles.demo_catalogue import make_demo_catalogue
from orderdesk.storage import LocalStorage
from orderdesk.workflow.app import build_app_graph


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="process waiting jobs, then exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings()
    worker_name = default_worker_name()
    storage = LocalStorage(settings.storage_dir)
    checkpointer = open_checkpointer(settings.database_url)
    try:
        graph = build_app_graph(storage, make_demo_catalogue(), checkpointer)
        handlers = {**HANDLERS, PROCESS_EMAIL: make_process_email(graph)}
        with connect(settings.database_url) as conn:
            if args.once:
                count = run_until_empty(conn, worker_name, handlers)
                print(f"processed {count} job(s)")
            else:
                run_forever(conn, worker_name, handlers=handlers)
    finally:
        checkpointer.conn.close()


if __name__ == "__main__":
    main()