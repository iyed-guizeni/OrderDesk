"""Tests for orderdesk.storage.LocalStorage."""

import hashlib
from pathlib import Path

import pytest

from orderdesk.storage import LocalStorage


def test_put_then_get_returns_same_bytes(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)

    key = storage.put(1, "attachments", b"hello")

    assert storage.get(key) == b"hello"


def test_key_contains_tenant_kind_and_hash(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    digest = hashlib.sha256(b"hello").hexdigest()

    key = storage.put(7, "raw", b"hello")

    assert key == f"tenants/7/raw/{digest[:2]}/{digest}"
    assert (tmp_path / key).is_file()


def test_same_content_twice_gives_same_key_and_one_file(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)

    first = storage.put(1, "attachments", b"same pdf")
    second = storage.put(1, "attachments", b"same pdf")

    assert first == second
    assert len([p for p in tmp_path.rglob("*") if p.is_file()]) == 1


def test_tenants_are_kept_apart(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)

    key_tenant_1 = storage.put(1, "attachments", b"same pdf")
    key_tenant_2 = storage.put(2, "attachments", b"same pdf")

    assert key_tenant_1 != key_tenant_2


def test_unknown_kind_is_refused(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)

    with pytest.raises(ValueError):
        storage.put(1, "secrets", b"x")  # type: ignore[arg-type]


def test_key_outside_storage_is_refused(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "storage")

    with pytest.raises(ValueError):
        storage.get("../../etc/passwd")