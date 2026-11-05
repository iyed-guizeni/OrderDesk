"""
Rules:
- temporary error (rate limit, timeout, provider down) -> wait a little, retry, then next provider
- invalid answer (no tool call, wrong fields)          -> retry once, then next provider
- permanent error (wrong key, unknown model)           -> next provider at once (retrying would fail again)
"""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from langchain_core.messages import BaseMessage
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

ErrorKind = Literal["temporary", "invalid", "permanent"]

ATTEMPTS_PER_PROVIDER = 2
WAIT_SECONDS = (1.0, 3.0)  # wait before the 2nd and 3rd attempt on the same provider


class ChatLike(Protocol):
    """Anything with invoke(messages): a real model, a FallbackLLM, or a FakeLLM in tests."""

    def invoke(self, messages: Sequence[BaseMessage], /) -> Any: ...


class InvalidAnswer(Exception):
    """The model answered, but not with the expected structure."""


class AllProvidersFailed(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("every LLM provider failed: " + " | ".join(errors))
        self.errors = errors


# sorting errors 

def _status_code(error: Exception) -> int | None:
    """Find an HTTP status code on the exceptions raised by the provider SDKs"""
    for candidate in (error, getattr(error, "response", None)):
        for attribute in ("status_code", "code", "status"):
            value = getattr(candidate, attribute, None)
            if isinstance(value, int):
                return value
    return None


def classify_error(error: Exception) -> ErrorKind:
    """Decide what to do with an error: retry (temporary/invalid) or move on (permanent)."""
    if isinstance(error, InvalidAnswer | ValidationError):
        return "invalid"
    if isinstance(error, TimeoutError | ConnectionError):
        return "temporary"
    status = _status_code(error)
    if status is not None:
        if status in (408, 409, 429) or status >= 500:
            return "temporary"   # timeout, rate limit, provider down
        if 400 <= status < 500:
            return "permanent"   # bad key (401/403), unknown model (404), bad request (400)
    name = type(error).__name__.lower()
    if "ratelimit" in name or "timeout" in name or "connection" in name:
        return "temporary"
    return "temporary"  # unknown error


# fallback

@dataclass(frozen=True)
class Provider:
    name: str        
    llm: ChatLike


@dataclass
class CallRecord:
    """What happened during the last call: for the audit log and monitoring."""

    provider: str | None = None
    attempts: int = 0
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


class FallbackLLM:
    """A ChatLike that tries each provider in order. Nodes use it exactly like one model."""

    def __init__(self, providers: Sequence[Provider], schema: type[BaseModel],
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if not providers:
            raise ValueError("FallbackLLM needs at least one provider")
        self.providers = list(providers)
        self.schema = schema
        self.sleep = sleep            # replaced by a no-op in tests, so they do not wait
        self.last_call = CallRecord()

    def invoke(self, messages: Sequence[BaseMessage], /) -> Any:
        record = CallRecord()
        start = time.perf_counter()
        for provider in self.providers:
            for attempt in range(1, ATTEMPTS_PER_PROVIDER + 1):
                record.attempts += 1
                try:
                    answer = provider.llm.invoke(messages)
                    if not isinstance(answer, self.schema):
                        raise InvalidAnswer(f"expected {self.schema.__name__}, got {type(answer).__name__}")
                except Exception as error:
                    kind = classify_error(error)
                    record.errors.append(f"{provider.name} #{attempt} {kind}: {type(error).__name__}: {error}"[:300])
                    log.warning("LLM call failed: %s", record.errors[-1])
                    if kind == "permanent":
                        log.error("check the configuration of %s", provider.name)
                        break                                   # this provider will not work: next one
                    if attempt < ATTEMPTS_PER_PROVIDER and kind == "temporary":
                        self.sleep(WAIT_SECONDS[attempt - 1])   # give the provider a moment
                    continue
                record.provider = provider.name
                record.seconds = round(time.perf_counter() - start, 2)
                self.last_call = record
                return answer
        record.seconds = round(time.perf_counter() - start, 2)
        self.last_call = record
        raise AllProvidersFailed(record.errors)


#  real providers 

def make_structured_llm(provider: str, model: str, schema: type[BaseModel]) -> ChatLike:
    """One provider, answering with `schema`. max_retries=0: FallbackLLM decides about retries."""
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        chat = ChatGoogleGenerativeAI(model=model, temperature=0, timeout=60, max_retries=0)
    elif provider == "mistral":
        from langchain_mistralai import ChatMistralAI

        chat = ChatMistralAI(model=model, temperature=0, timeout=60, max_retries=0)
    elif provider == "groq":
        from langchain_groq import ChatGroq

        chat = ChatGroq(model=model, temperature=0, timeout=60, max_retries=0)
    else:
        raise ValueError(f"unknown provider {provider!r}")
    return chat.with_structured_output(schema, method="function_calling")


def make_fallback_llm(chain: Sequence[tuple[str, str]], schema: type[BaseModel]) -> FallbackLLM:
    """chain: [("mistral", "mistral-small-latest"), ("groq", "llama-3.3-70b-versatile")]"""
    providers = [Provider(f"{p}:{m}", make_structured_llm(p, m, schema)) for p, m in chain]
    return FallbackLLM(providers, schema)