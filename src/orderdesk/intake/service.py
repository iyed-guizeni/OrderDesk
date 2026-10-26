"""Store an email, dedupe on Message-ID and fingerprint, early link by thread or PO, create a job.

Round 1 (this file): parse -> save files -> insert rows, with dedupe on Message-ID.
Later: content fingerprint, early link, job creation.
"""

from dataclasses import dataclass
from typing import Literal

import psycopg

from orderdesk.domain.models import ParsedEmail
from orderdesk.intake.parser import EmailParseError, parse_email
from orderdesk.storage import Storage
from orderdesk.jobs.queue import enqueue

Status = Literal["new", "duplicate", "rejected"]


@dataclass(frozen=True)
class IngestResult:
    status: Status
    email_id: int | None = None
    message_id: str | None = None
    reason: str | None = None


def ingest_email(conn: psycopg.Connection, storage: Storage, tenant_id: int, raw: bytes) -> IngestResult:
    """Parse, store and record one email. Safe to call again with the same email.

    Order matters: files are saved BEFORE the database rows. If we crash in between,
    we only leave an unused file; a retry saves the same file under the same key.
    """
    try:
        email = parse_email(raw)
    except EmailParseError as error:
        return IngestResult(status="rejected", reason=str(error))

    raw_key = storage.put(tenant_id, "raw", raw)
    attachment_keys = [storage.put(tenant_id, "attachments", a.data) for a in email.attachments]

    with conn.transaction():  # the email row and its attachment rows are saved together, or not at all
        email_id = _insert_email(conn, tenant_id, email, raw_key)
        if email_id is None:
            existing_id = _find_email_id(conn, tenant_id, email.message_id)
            return IngestResult(status="duplicate", email_id=existing_id, message_id=email.message_id)
        _insert_attachments(conn, tenant_id, email_id, email, attachment_keys)
        #create a job
        enqueue(conn, tenant_id, email_id)

    return IngestResult(status="new", email_id=email_id, message_id=email.message_id)


def _insert_email(conn: psycopg.Connection, tenant_id: int, email: ParsedEmail, raw_key: str) -> int | None:
    """Insert the email row. Returns its id, or None if this Message-ID is already stored."""
    row = conn.execute(
        """
        INSERT INTO emails (
            tenant_id, message_id, in_reply_to, references_ids,
            from_address, from_name, reply_to, to_addresses, cc_addresses,
            subject, sent_at, body_text, raw_key, raw_sha256
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT ON CONSTRAINT emails_tenant_message_id_unique DO NOTHING
        RETURNING id
        """,
        (
            tenant_id, email.message_id, email.in_reply_to, email.references_ids,
            email.from_address, email.from_name, email.reply_to, email.to_addresses, email.cc_addresses,
            email.subject, email.sent_at, email.body_text, raw_key, email.raw_sha256,
        ),
    ).fetchone()
    return row[0] if row else None


def _find_email_id(conn: psycopg.Connection, tenant_id: int, message_id: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM emails WHERE tenant_id = %s AND message_id = %s",
        (tenant_id, message_id),
    ).fetchone()
    return row[0] if row else None


def _insert_attachments(
    conn: psycopg.Connection, tenant_id: int, email_id: int, email: ParsedEmail, keys: list[str]
) -> None:
    for attachment, key in zip(email.attachments, keys, strict=True):
        conn.execute(
            """
            INSERT INTO attachments (tenant_id, email_id, filename, content_type, size_bytes, sha256, storage_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                tenant_id, email_id, attachment.filename, attachment.content_type,
                attachment.size_bytes, attachment.sha256, key,
            ),
        )