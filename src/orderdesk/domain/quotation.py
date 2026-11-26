"""Models for deterministic ERP draft-quotation writes and alias outcomes."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ORIGIN_PREFIX = "OrderDesk"
AliasOutcome = Literal["learned", "unchanged", "forgotten"]


def orderdesk_origin(tenant_id: int, email_id: int) -> str:
    """Return the stable OrderDesk origin used to make a write idempotent."""
    return f"{ORIGIN_PREFIX} {tenant_id}-{email_id}"


class QuotationLine(BaseModel):
    """One line in a draft ERP quotation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    quantity: float = Field(gt=0)
    unit_price: float | None = None


class QuotationRequest(BaseModel):
    """All the data needed to create or idempotently retrieve a draft quotation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    origin: str
    customer_id: int
    client_ref: str | None
    lines: list[QuotationLine] = Field(min_length=1)
