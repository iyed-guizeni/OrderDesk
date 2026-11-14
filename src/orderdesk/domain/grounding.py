"""Grounding issues: something the LLM extracted that we cannot find in what the client sent.
An issue is not an error: the order goes on, and the reviewer sees the issue as a flag.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

IssueKind = Literal[
    "line_not_in_source",  # the line's raw_text is nowhere in the email or attachments: likely invented
    "line_only_in_quoted_text",  # found only in a quoted earlier message (often our own confirmation)
    "quantity_not_in_line",  # the quantity does not appear in the line's raw_text
    "code_not_in_source",  # the product code the client "wrote" is not in the email or attachments
    "po_not_in_source",  # the PO number is not in the subject, email or attachments
]


class GroundingIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: IssueKind
    line_index: int | None = None  # None for order-level fields (po_number)
    value: str  # what the LLM wrote, for the reviewer and the logs
