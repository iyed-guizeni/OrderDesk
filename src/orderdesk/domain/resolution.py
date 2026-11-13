"""What the Resolve step produces: for each extracted line, which product it is, or why we could not tell.

Resolve answers one question only: "which product?". Unit, price, stock and PO checks come later (Validate).
An unresolved line always carries a reason: the reason decides who continues (the agent or a person).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orderdesk.domain.catalogue import Candidate, Customer, Product

Method = Literal["alias", "exact_code", "search", "llm_choice"]  # later: "agent"
Reason = Literal[
    "product_missing",  # the client wrote no product ("et 10 boîtes aussi"): only the client knows
    "previous_order",  # "les mêmes que la dernière fois": the answer is in past orders
    "variant_missing",  # the product is clear but exists in several finishes and none was written
    "no_candidates",  # product words exist but the search found nothing close
    "ambiguous",  # several different products match equally well: the LLM choice step reads the email
    "llm_said_none",  # the LLM read the email and none of the candidates clearly fits
]
NextStep = Literal["done", "agent", "human"]


class LineResolution(BaseModel):
    model_config = ConfigDict(frozen=True)

    line_index: int  # position in ExtractedOrder.lines
    status: Literal["resolved", "unresolved"]
    product: Product | None = None
    method: Method | None = None
    reason: Reason | None = None
    candidates: list[Candidate] = []  # what the search found: for the LLM choice, the agent and the reviewer
    evidence: str | None = None  # why an LLM (or later the agent) chose this product: shown to the reviewer
    next_step: NextStep

    @model_validator(mode="after")
    def _consistent(self) -> "LineResolution":
        if self.status == "resolved" and (self.product is None or self.method is None or self.next_step != "done"):
            raise ValueError("a resolved line needs a product and a method, and is done")
        if self.status == "unresolved" and (self.reason is None or self.product is not None):
            raise ValueError("an unresolved line needs a reason and no product")
        return self


class CandidateChoice(BaseModel):
    """The LLM's answer for one ambiguous line. Bound as a tool: the descriptions reach the model."""

    model_config = ConfigDict(extra="forbid")

    evidence: str = Field(
        description=(
            "The client's exact words (from the line or the rest of the email) that point to one candidate. "
            "With NONE: a few words on what is missing to decide."
        )
    )
    code: str = Field(description="One candidate code exactly as listed, or NONE if no candidate clearly fits.")


class ResolvedOrder(BaseModel):
    model_config = ConfigDict(frozen=True)

    customer: Customer | None  # None: the sender is not a known customer
    lines: list[LineResolution]
