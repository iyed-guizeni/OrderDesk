"""Constants and helpers shared by the integration tests."""

from pathlib import Path

TEST_TENANT = 999
OTHER_TEST_TENANT = 998
TEST_TENANTS = [TEST_TENANT, OTHER_TEST_TENANT]

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "emails"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()