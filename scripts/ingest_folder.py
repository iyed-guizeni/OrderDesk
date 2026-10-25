import sys
from collections import Counter
from pathlib import Path

from orderdesk.config import load_settings
from orderdesk.db.connection import connect
from orderdesk.intake.mailbox import read_eml_folder
from orderdesk.intake.service import ingest_email
from orderdesk.storage import LocalStorage

DEFAULT_FOLDER = Path("tests/fixtures/emails")


def main() -> None:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_FOLDER
    settings = load_settings()
    storage = LocalStorage(settings.storage_dir)
    counts: Counter[str] = Counter()

    with connect(settings.database_url) as conn:
        for name, raw in read_eml_folder(folder):
            result = ingest_email(conn, storage, settings.tenant_id, raw)
            counts[result.status] += 1
            detail = result.reason or f"email id {result.email_id}"
            print(f"{result.status:<9} {name}  ({detail})")

    print(f"\nnew: {counts['new']}  duplicate: {counts['duplicate']}  rejected: {counts['rejected']}")


if __name__ == "__main__":
    main()