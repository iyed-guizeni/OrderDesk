from langchain_google_genai import ChatGoogleGenerativeAI
from typing import Protocol, Any
from collections.abc import Sequence
from langchain_core.messages import BaseMessage



llm_classification = ChatGoogleGenerativeAI(model="gemini-3.5-flash-lite")

class ChatLike(Protocol):
    def invoke(self, messages: Sequence[BaseMessage])->Any: ...
          