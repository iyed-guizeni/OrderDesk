
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Label = Literal["new_order", "amendment", "cancellation", "quote_request", "after_sales", "other"]



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


class EmailClassification(BaseModel):
    """Classify one email received by the sales team of a wholesale distributor."""

    model_config = ConfigDict(extra="forbid")

    evidence: str = Field(
        description="The sentence from the email that decides the label, copied exactly (max ~20 words)."
    )
    label: Label = Field(
        description=(
            "new_order: the client commits to buying products or spare parts (quantities, a purchase order, "
            "'same as last time'). "
            "amendment: the client changes an order already placed (quantity, model, finish, date, address, adds a line). "
            "cancellation: the client cancels a whole order or a line of it. "
            "quote_request: the client asks for a price, availability or lead time WITHOUT committing yet. "
            "after_sales: a problem with a product already delivered or installed (leak, defect, broken or missing "
            "part, warranty claim, return, installation problem). Ordering a spare part with no problem is new_order. "
            "other: anything else (invoices, payments, delivery tracking, catalogues, consumers, advertising)."
        )
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description=(
            "high: the intent is explicit. medium: probable but one detail is unclear. "
            "low: you are guessing between two labels."
        )
    )
    also_contains: list[Label] = Field(
        default_factory=list,
        description="Other intents in the SAME email (e.g. cancels one order and places another). Usually empty.",
    )
    
    
