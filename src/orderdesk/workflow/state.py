from orderdesk.domain.models import ParsedEmail
from typing import TypedDict
from orderdesk.jobs.queue import Job
from pydantic import BaseModel, ConfigDict
from orderdesk.domain.classification import EmailContext
    

class WorkflowState(TypedDict):
    email: EmailContext
    
    
    