
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Literal, Protocol

Kind = Literal["raw", "attachments"]
_KINDS = ("raw", "attachments")


class Storage(Protocol):
    def put(self, tenant_id: int, kind: Kind, data: bytes) -> str: ...
    def get(self, key: str) -> bytes: ...


def make_key(tenant_id: int, kind: Kind, data: bytes) -> str:
    if kind not in _KINDS:
        raise ValueError(f"unknown storage kind: {kind!r}")
    digest = hashlib.sha256(data).hexdigest()
    return f"tenants/{tenant_id}/{kind}/{digest[:2]}/{digest}"


class LocalStorage:
    """Stores files in a local folder."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def put(self, tenant_id: int, kind: Kind, data: bytes) -> str:
        """Save the bytes and return their key. Does nothing if the same content is already stored."""
        key = make_key(tenant_id, kind, data)
        path = self._path(key)
        if path.exists():
            return key
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temporary file, then rename: a crash never leaves a half-written file.
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            tmp.write(data)
            tmp_name = tmp.name
        os.replace(tmp_name, path)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):  # refuse keys like "../../secret"
            raise ValueError(f"invalid storage key: {key!r}")
        return path