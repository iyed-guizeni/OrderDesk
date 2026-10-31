from typing import TypedDict
from orderdesk.domain.email import EmailContext
from orderdesk.domain.classification import EmailClassification

class WorkflowState(TypedDict):
    email: EmailContext
    classification: EmailClassification
    
    
    