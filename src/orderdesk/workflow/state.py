from typing import TypedDict
from pydantic import BaseModel
from orderdesk.domain.email import EmailContext
from orderdesk.domain.classification import EmailClassification

class WorkflowState(BaseModel):
    email: EmailContext
    classification: EmailClassification | None = None
    
    
    