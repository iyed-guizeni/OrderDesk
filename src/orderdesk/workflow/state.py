from pydantic import BaseModel

from orderdesk.domain.classification import EmailClassification
from orderdesk.domain.email import EmailContext
from orderdesk.domain.extraction import ExtractedOrder
from orderdesk.domain.grounding import GroundingIssue
from orderdesk.domain.resolution import ResolvedOrder


class WorkflowState(BaseModel):
    email: EmailContext
    classification: EmailClassification | None = None
    extracted_order: ExtractedOrder | None = None
    grounding_issues: list[GroundingIssue] = []  # what Extract wrote that we cannot find in the email
    resolved_order: ResolvedOrder | None = None
