"""Builders for test data: each test changes only the fields it is about."""

import io
from datetime import datetime, timezone

from orderdesk.domain.email import AttachmentRef, EmailContext

FRIDAY = datetime(2026, 11, 6, 9, 30, tzinfo=timezone.utc)


def make_email(**changes) -> EmailContext:
    """A typical order email with no attachments."""
    fields = {
        "tenant_id": 999,
        "email_id": 7,
        "sender": "showroom@bains-and-co.example",
        "sent_at": FRIDAY,
        "subject": "Bon de commande 4471",
        "body": "Bonjour,\n2 mitigeurs Profilo noir brossé, livraison avant vendredi.\nMerci",
        "is_reply": False,
        "attachments": [],
    }
    return EmailContext(**{**fields, **changes})


def make_attachment(filename: str, content_type: str) -> AttachmentRef:
    return AttachmentRef(filename=filename, content_type=content_type, storage_key=f"tenants/999/attachments/{filename}")


def make_pdf(text: str) -> bytes:
    """A real one-page PDF with one line of text (reportlab is a dev dependency)."""
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawString(72, 750, text)
    pdf.save()
    return buffer.getvalue()
