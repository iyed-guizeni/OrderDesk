
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from orderdesk.config import load_settings
from orderdesk.db.connection import connect
from orderdesk.storage import LocalStorage
from tests.integration.helpers import TEST_TENANTS


def _clean(connection: psycopg.Connection) -> None:
    # Deleting the emails also deletes their attachments and jobs (ON DELETE CASCADE).
    connection.execute("DELETE FROM emails WHERE tenant_id = ANY(%s)", (TEST_TENANTS,))


@pytest.fixture
def database_url() -> str:
    try:
        return load_settings().database_url
    except RuntimeError as error:
        pytest.skip(f"database not configured: {error}")


@pytest.fixture
def conn(database_url: str) -> Iterator[psycopg.Connection]:
    try:
        connection = connect(database_url)
    except psycopg.OperationalError as error:
        pytest.skip(f"database not available: {error}")
    _clean(connection)
    yield connection
    _clean(connection)
    connection.close()


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path)