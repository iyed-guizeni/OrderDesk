from orderdesk.domain.models import ParsedEmail
from typing import TypedDict

class MainState(TypedDict):
    email: ParsedEmail
    