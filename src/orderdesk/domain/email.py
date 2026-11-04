
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field




class AttachmentRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    filename: str
    content_type: str          
    storage_key: str           


class EmailContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: int
    email_id: int
    sender: str
    subject: str
    body: str
    is_reply: bool
    attachments: list[AttachmentRef] = []
    attachment_excerpt: str | None = None #only when the mail body is (almost) empty: for classification context enrichment



    
