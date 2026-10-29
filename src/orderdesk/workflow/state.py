from orderdesk.domain.models import ParsedEmail
from typing import TypedDict
from orderdesk.jobs.queue import Job

class MainState(TypedDict):
    email: ParsedEmail
    job: Job
    
    