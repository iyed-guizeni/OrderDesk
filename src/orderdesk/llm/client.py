from langchain_google_genai import ChatGoogleGenerativeAI
from typing import Protocol, Any
from collections.abc import Sequence
from langchain_core.messages import BaseMessage
from orderdesk.domain.classification import EmailClassification



class ChatLike(Protocol):
    def invoke(self, messages: Sequence[BaseMessage])->Any: ...
        
class ChatWithGemini:
    def invoke(self, messages: Sequence[BaseMessage])->Any:
        llm_classification = ChatGoogleGenerativeAI(model="gemini-3.5-flash-lite")
        llm = llm_classification.with_structured_output(EmailClassification)
        return llm.invoke(messages)
        
    