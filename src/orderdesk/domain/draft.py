"""What Validate produces: the draft order a person reviews (prices from the ERP, warnings, total).

No customer name or address here: the id is enough, the review screen looks the name up.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from orderdesk.domain.catalogue import Product

LineIssue = Literal["no_product", "no_quantity", "no_price", "discontinued", "unit_differs"]
OrderIssueKind = Literal["po_already_used", "unknown_customer", "no_lines"]


class DraftLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    line_index: int
    raw_text: str
    quantity: float | None
    unit: str | None
    product: Product | None
    unit_price: float | None
    line_total: float | None
    issues: list[LineIssue] = []
    evidence: str | None = None


class OrderIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: OrderIssueKind
    detail: str


class DraftOrder(BaseModel):
    model_config = ConfigDict(frozen=True)

    customer_id: int | None
    po_number: str | None
    lines: list[DraftLine]
    order_issues: list[OrderIssue] = []
    total: float

    @property
    def blocking(self) -> list[OrderIssue]:
        """The issues that forbid approval: every order issue, in this batch."""
        return list(self.order_issues)
