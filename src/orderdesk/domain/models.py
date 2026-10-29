"""Pydantic models: """

from datetime import datetime
from pydantic import BaseModel, ConfigDict

class ParsedAttachment(BaseModel):
    model_config = ConfigDict(frozen=True)

    filename: str | None
    content_type: str          # e.g. "application/pdf", "text/csv"
    data: bytes                # the file content
    size_bytes: int
    sha256: str                # fingerprint of `data`


class ParsedEmail(BaseModel):
    model_config = ConfigDict(frozen=True)

    message_id: str                     
    message_id_is_synthetic: bool       # True if the email had no Message-ID and we made one
    in_reply_to: str | None
    references_ids: list[str]
    from_address: str                   
    from_name: str | None               
    reply_to: str | None
    to_addresses: list[str]
    cc_addresses: list[str]
    subject: str | None
    sent_at: datetime | None            
    body_text: str | None
    attachments: list[ParsedAttachment]
    raw_sha256: str                     # fingerprint of the whole original .eml


