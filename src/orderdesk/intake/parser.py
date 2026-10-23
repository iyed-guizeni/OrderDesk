"""Turn raw .eml bytes into a ParsedEmail (headers, text body, attachments).

Pure function: no database, no files, no network. Easy to test.
"""

import hashlib
import re
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser

from orderdesk.domain.models import ParsedAttachment, ParsedEmail

# A message id looks like <something@domain>. Used to read In-Reply-To and References.
_MESSAGE_ID_RE = re.compile(r"<[^<>\s]+>")


class EmailParseError(ValueError):
    """The email cannot be used (for example: no sender)."""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_email(raw: bytes) -> ParsedEmail:
    """Parse one email. Raises EmailParseError if it has no usable sender."""
    msg = BytesParser(policy=policy.default).parsebytes(raw) 
    raw_sha256 = sha256_hex(raw)

    from_name, from_address = _first_address(msg, "From")
    if not from_address:
        raise EmailParseError("email has no sender address (From header)")

    message_id, is_synthetic = _message_id(msg, raw_sha256)
    in_reply_to_ids = _message_ids(msg, "In-Reply-To")

    return ParsedEmail(
        message_id=message_id,
        message_id_is_synthetic=is_synthetic,
        in_reply_to=in_reply_to_ids[0] if in_reply_to_ids else None,
        references_ids=_message_ids(msg, "References"),
        from_address=from_address,
        from_name=from_name,
        reply_to=_first_address(msg, "Reply-To")[1],
        to_addresses=_all_addresses(msg, "To"),
        cc_addresses=_all_addresses(msg, "Cc"),
        subject=_header_text(msg, "Subject"),
        sent_at=_sent_at(msg),
        body_text=_body_text(msg),
        attachments=_attachments(msg),
        raw_sha256=raw_sha256,
    )


# ---------- headers ----------

def _header_text(msg: EmailMessage, name: str) -> str | None:
    """Header value as clean text, or None. Never raises on a malformed header."""
    try:
        value = msg.get(name)
    except Exception:  # noqa: BLE001 - a broken header must not stop intake
        return None
    if value is None:
        return None
    text = " ".join(str(value).split())  # remove line breaks and repeated spaces
    return text or None


def _message_id(msg: EmailMessage, raw_sha256: str) -> tuple[str, bool]:
    """The Message-ID, or a stable one built from the content if it is missing."""
    ids = _message_ids(msg, "Message-ID")
    if ids:
        return ids[0], False
    # Same content -> same synthetic id, so exact duplicates are still caught.
    return f"<{raw_sha256}@orderdesk.synthetic>", True


def _message_ids(msg: EmailMessage, name: str) -> list[str]:
    """All <...@...> ids found in a header, in order, without repeats."""
    text = _header_text(msg, name)
    if not text:
        return []
    seen: list[str] = []
    for found in _MESSAGE_ID_RE.findall(text):
        if found not in seen:
            seen.append(found)
    return seen


def _addresses(msg: EmailMessage, name: str) -> list[tuple[str | None, str]]:
    """(display name, lower-case address) pairs for an address header."""
    text = _header_text(msg, name)
    if not text:
        return []
    result: list[tuple[str | None, str]] = []
    for display_name, address in getaddresses([text]):
        address = address.strip().lower()
        if "@" in address:
            result.append((display_name.strip() or None, address))
    return result


def _first_address(msg: EmailMessage, name: str) -> tuple[str | None, str | None]:
    found = _addresses(msg, name)
    return found[0] if found else (None, None)


def _all_addresses(msg: EmailMessage, name: str) -> list[str]:
    return [address for _, address in _addresses(msg, name)]


def _sent_at(msg: EmailMessage) -> datetime | None:
    """The Date header as a timezone-aware datetime (UTC if no timezone was given)."""
    text = _header_text(msg, "Date")
    if not text:
        return None
    try:
        value = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value


# ---------- body ----------

def _body_text(msg: EmailMessage) -> str | None:
    """The plain-text body; if there is only HTML, convert it to text."""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return None
    text = _part_text(part)
    if part.get_content_type() == "text/html":
        text = html_to_text(text)
    text = text.strip()
    return text or None


def _part_text(part: EmailMessage) -> str:
    try:
        return part.get_content()
    except (LookupError, UnicodeDecodeError):  # unknown or wrong charset
        payload = part.get_payload(decode=True) or b""
        return payload.decode("utf-8", errors="replace")


class _HTMLToText(HTMLParser):
    _BREAKS = {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "table"}

    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self._BREAKS:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in self._BREAKS:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.chunks.append(data)


def html_to_text(html: str) -> str:
    """Very simple HTML-to-text: keeps the words and line breaks, drops tags, scripts and styles."""
    parser = _HTMLToText()
    parser.feed(html)
    lines = (" ".join(line.split()) for line in "".join(parser.chunks).splitlines())
    return "\n".join(line for line in lines if line)


# ---------- attachments ----------

def _attachments(msg: EmailMessage) -> list[ParsedAttachment]:
    result: list[ParsedAttachment] = []
    for part in msg.iter_attachments():
        data = _part_bytes(part)
        result.append(
            ParsedAttachment(
                filename=part.get_filename(),
                content_type=part.get_content_type(),
                data=data,
                size_bytes=len(data),
                sha256=sha256_hex(data),
            )
        )
    return result


def _part_bytes(part: EmailMessage) -> bytes:
    """The decoded content of an attachment."""
    data = part.get_payload(decode=True)
    if isinstance(data, bytes):
        return data
    # A forwarded email attached as a file (message/rfc822): keep it as .eml bytes.
    inner = part.get_payload()
    if isinstance(inner, list) and inner:
        return inner[0].as_bytes()
    return b""