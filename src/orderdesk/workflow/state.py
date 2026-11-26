from pydantic import BaseModel

from orderdesk.domain.classification import EmailClassification
from orderdesk.domain.draft import DraftOrder
from orderdesk.domain.email import EmailContext
from orderdesk.domain.extraction import ExtractedOrder
from orderdesk.domain.grounding import GroundingIssue
from orderdesk.domain.resolution import ResolvedOrder
from orderdesk.domain.review import ReviewDecision


class WorkflowState(BaseModel):
    email: EmailContext
    classification: EmailClassification | None = None
    extracted_order: ExtractedOrder | None = None
    grounding_issues: list[GroundingIssue] = []  # what Extract wrote that we cannot find in the email
    resolved_order: ResolvedOrder | None = None
    draft_order: DraftOrder | None = None
    review_decision: ReviewDecision | None = None
    approved_order: DraftOrder | None = None
    erp_quotation_id: str | None = None
    learned_aliases: int = 0
    forgotten_aliases: int = 0
