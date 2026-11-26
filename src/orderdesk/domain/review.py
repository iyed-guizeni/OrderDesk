"""A person's answer at the Review pause. "Edit" is an approve with changes. Code checks it (checks/order.py)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LineChange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    line_index: int
    code: str | None = None
    quantity: float | None = None
    remove: bool = False


class ReviewDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["approve", "reject"]
    reviewer: str = Field(min_length=1)
    changes: list[LineChange] = []
    comment: str | None = None
