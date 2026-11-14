"""What the company sells and who buys it, as the resolver sees it (read from the ERP)."""

import datetime

from pydantic import BaseModel, ConfigDict


class Product(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str  # the ERP reference (Odoo default_code), e.g. "PF60375"
    name: str  # e.g. "Mitigeur lavabo Profilo"
    family: str  # products that differ only by finish share a family (Odoo product template)
    finish: str | None = None  # e.g. "noir brossé"; None when the product has no finish
    uom: str = "piece"  # unit the ERP sells it in
    active: bool = True  # False = discontinued (checked later, not by the resolver)


class Customer(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    name: str


class PastOrderLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    finish: str | None = None
    quantity: float


class PastOrder(BaseModel):
    """A CONFIRMED order (Odoo state "sale"), never an OrderDesk draft: "same as last time" must not copy a copy."""

    model_config = ConfigDict(frozen=True)

    order_id: str  # e.g. "SO-2291"
    date: datetime.date
    lines: list[PastOrderLine]


class Candidate(BaseModel):
    """A search result: a product and how well it matches the client's words (0 to 1)."""

    model_config = ConfigDict(frozen=True)

    product: Product
    score: float
