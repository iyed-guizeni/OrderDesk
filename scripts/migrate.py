"""
Apply pending database migrations
"""

from orderdesk.config import load_settings
from orderdesk.db.connection import connect
from orderdesk.db.migrate import apply_migrations


def main() -> None:
    settings = load_settings()
    with connect(settings.database_url) as conn:
        applied = apply_migrations(conn)
    if applied:
        for name in applied:
            print(f"applied {name}")
    else:
        print("database is up to date")


if __name__ == "__main__":
    main()