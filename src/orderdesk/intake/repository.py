
import psycopg

from orderdesk.domain.email import AttachmentRef, EmailContext


def load_email_context(conn: psycopg.Connection, tenant_id: int, email_id: int) -> EmailContext:
    """What the workflow needs to know about one email. Attachment bytes stay in storage."""
    row = conn.execute(
        """
        SELECT from_address, subject, body_text, in_reply_to, references_ids
        FROM emails
        WHERE tenant_id = %s AND id = %s
        """,
        (tenant_id, email_id),
    ).fetchone()
    if row is None:
        raise LookupError(f"email {email_id} not found for tenant {tenant_id}")
    sender, subject, body_text, in_reply_to, references_ids = row

    attachments = [
        AttachmentRef(filename=filename, content_type=content_type, storage_key=storage_key)
        for filename, content_type, storage_key in conn.execute(
            """
            SELECT filename, content_type, storage_key
            FROM attachments
            WHERE tenant_id = %s AND email_id = %s
            ORDER BY id
            """,
            (tenant_id, email_id),
        )
    ]

    return EmailContext(
        tenant_id=tenant_id,
        email_id=email_id,
        sender=sender,
        subject=subject or "",
        body=body_text or "",
        is_reply=in_reply_to is not None or bool(references_ids),
        attachments=attachments,
    )