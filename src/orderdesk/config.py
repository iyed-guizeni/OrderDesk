import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database_url: str
    storage_dir: Path
    tenant_id: int


def load_settings() -> Settings:
    load_dotenv()
    database_url = os.environ.get("APP_DATABASE_URL")
    if not database_url:
        raise RuntimeError("APP_DATABASE_URL is missing. Add it to your .env file.")
    return Settings(
        database_url=database_url,
        storage_dir=Path(os.environ.get("STORAGE_DIR", "data/storage")),
        tenant_id=int(os.environ.get("TENANT_ID", "1")),
    )