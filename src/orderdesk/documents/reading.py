

from orderdesk.documents.text import extract_text
from orderdesk.domain.email import EmailContext
from orderdesk.storage import Storage


def read_attachments(email: EmailContext, storage: Storage) -> tuple[list[tuple[str, str]], list[str]]:
    """Text of each attachment we can read, and the names of those we cannot."""
    readable, unreadable = [], []
    for attachment in email.attachments:
        text = extract_text(storage.get(attachment.storage_key), attachment.content_type, attachment.filename)
        if text:
            readable.append((attachment.filename, text))
        else:
            unreadable.append(attachment.filename)
    return readable, unreadable