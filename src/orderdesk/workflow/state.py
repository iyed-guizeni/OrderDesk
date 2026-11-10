from typing import TypedDict
from pydantic import BaseModel
from orderdesk.domain.email import EmailContext
from orderdesk.domain.classification import EmailClassification
from orderdesk.domain.extraction import ExtractedOrder

class WorkflowState(BaseModel):
    email: EmailContext
    classification: EmailClassification | None = None
    extracted_order: ExtractedOrder | None = None
    
    
    