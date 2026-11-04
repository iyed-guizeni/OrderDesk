"""Give the classifier a look at the attachment when the email body says (almost) nothing."""

from orderdesk.documents.text import extract_text
from orderdesk.domain.email import EmailContext
from orderdesk.storage import Storage

SHORT_BODY_WORDS = 20      # below this, the body alone is not enough to classify
MAX_EXCERPT_CHARS = 1500   # enough to see what the document is


def add_attachment_excerpt(email: EmailContext, storage: Storage) -> EmailContext:
    """Return the email with `attachment_excerpt` filled, if the body is short and a document is readable."""
    if len(email.body.split()) >= SHORT_BODY_WORDS:
        return email
    for attachment in email.attachments:
        text = extract_text(storage.get(attachment.storage_key), attachment.content_type, attachment.filename)
        if text:
            excerpt = f"[{attachment.filename}]\n{text[:MAX_EXCERPT_CHARS]}"
            return email.model_copy(update={"attachment_excerpt": excerpt})
    return email