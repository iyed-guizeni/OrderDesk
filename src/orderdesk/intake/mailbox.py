"""Read emails from a source: a folder of .eml files first, IMAP  and webhook later."""

from collections.abc import Iterator
from pathlib import Path


def read_eml_folder(folder: Path) -> Iterator[tuple[str, bytes]]:
    """Yield (file name, raw bytes) for every .eml file in the folder, in name order."""
    if not folder.is_dir():
        raise FileNotFoundError(f"folder not found: {folder}")
    for path in sorted(folder.glob("*.eml")):
        yield path.name, path.read_bytes()