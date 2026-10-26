"""Start a worker.

    python scripts/run_worker.py          # runs until Ctrl+C
    python scripts/run_worker.py --once   # processes waiting jobs, then exits
"""

import argparse
import logging

from orderdesk.config import load_settings
from orderdesk.db.connection import connect
from orderdesk.jobs.worker import default_worker_name, run_forever, run_until_empty


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="process waiting jobs, then exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings()
    worker_name = default_worker_name()

    with connect(settings.database_url) as conn:
        if args.once:
            count = run_until_empty(conn, worker_name)
            print(f"processed {count} job(s)")
        else:
            run_forever(conn, worker_name)


if __name__ == "__main__":
    main()