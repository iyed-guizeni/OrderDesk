"""Turn an attachment into plain text (PDF, Excel, CSV, plain text). Images and unknown types give None.
"""

import csv
import io
import logging
from pypdf import PdfReader
from openpyxl import load_workbook

log = logging.getLogger(__name__)

MAX_PDF_PAGES = 3
MAX_SHEET_ROWS = 60


def extract_text(data: bytes, content_type: str, filename: str) -> str | None:
    """Best-effort plain text of a document, or None if we cannot (or should not) read it."""
    kind = _kind(content_type, filename)
    try:
        if kind == "pdf":
            return _pdf_text(data)
        if kind == "xlsx":
            return _xlsx_text(data)
        if kind == "csv":
            return _csv_text(data)
        if kind == "text":
            return _decode(data)
    except Exception as error:  # broken, encrypted or strange file
        log.warning("could not read attachment %s: %s", filename, error)
    return None


def _kind(content_type: str, filename: str) -> str | None:
    name = filename.lower()
    if content_type == "application/pdf" or name.endswith(".pdf"):
        return "pdf"
    if name.endswith(".xlsx") or "spreadsheetml" in content_type:
        return "xlsx"
    if name.endswith(".csv") or content_type == "text/csv":
        return "csv"
    if content_type == "text/plain" or name.endswith(".txt"):
        return "text"
    return None  # images, .doc, zip...

def _pdf_text(data: bytes) -> str | None:

    reader = PdfReader(io.BytesIO(data))
    pages = [page.extract_text() or "" for page in reader.pages[:MAX_PDF_PAGES]]
    text = "\n".join(pages).strip()
    return text or None  # a scanned PDF (image only) has no text: OCR would be needed


def _xlsx_text(data: bytes) -> str | None:

    sheet = load_workbook(io.BytesIO(data), read_only=True, data_only=True).active
    lines = []
    for row in sheet.iter_rows(max_row=MAX_SHEET_ROWS, values_only=True):
        cells = [str(cell) for cell in row if cell is not None]
        if cells:
            lines.append(" | ".join(cells))
    return "\n".join(lines) or None


def _csv_text(data: bytes) -> str | None:
    text = _decode(data)
    if text is None:
        return None
    try:
        dialect = csv.Sniffer().sniff(text[:2000], delimiters=",;\t")  # French CSV often uses ";"
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))[:MAX_SHEET_ROWS]
    return "\n".join(" | ".join(cell.strip() for cell in row) for row in rows if any(row)) or None


def _decode(data: bytes) -> str | None:
    for encoding in ("utf-8-sig", "cp1252"):  
        try:
            return data.decode(encoding).strip() or None
        except UnicodeDecodeError:
            continue
    return None