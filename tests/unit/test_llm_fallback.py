"""Unit tests for error handling in the LLM client. No network: providers are FakeLLMs."""

import pytest

from orderdesk.domain.classification import EmailClassification
from orderdesk.llm.client import AllProvidersFailed, FallbackLLM, Provider, classify_error
from tests.fakes import FakeLLM

GOOD = EmailClassification( evidence="Ci-joint notre BC", label="new_order", confidence="high")
MESSAGES = ["hello"]


class HTTPError(Exception):
    """Looks like the errors raised by provider SDKs: they carry an HTTP status code."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def make(*providers: tuple[str, FakeLLM]) -> FallbackLLM:
    waits: list[float] = []
    llm = FallbackLLM([Provider(name, fake) for name, fake in providers], EmailClassification, sleep=waits.append)
    llm.waits = waits  # type: ignore[attr-defined]  # lets tests check the waiting without waiting
    return llm


# ---------- sorting errors ----------

@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (HTTPError(429), "temporary"),
        (HTTPError(503), "temporary"),
        (TimeoutError(), "temporary"),
        (HTTPError(401), "permanent"),
        (HTTPError(404), "permanent"),
    ],
)
def test_classify_error(error: Exception, kind: str) -> None:
    assert classify_error(error) == kind


# ---------- fallback ----------

def test_first_provider_answers() -> None:
    llm = make(("mistral", FakeLLM(GOOD)))

    assert llm.invoke(MESSAGES) == GOOD
    assert (llm.last_call.provider, llm.last_call.attempts, llm.last_call.errors) == ("mistral", 1, [])


def test_temporary_error_is_retried_after_a_wait() -> None:
    llm = make(("mistral", FakeLLM(HTTPError(429), GOOD)))

    assert llm.invoke(MESSAGES) == GOOD
    assert llm.last_call.attempts == 2
    assert llm.waits == [1.0]


def test_invalid_answer_then_next_provider() -> None:
    llm = make(("mistral", FakeLLM(None)), ("groq", FakeLLM(GOOD)))  # mistral never calls the tool

    assert llm.invoke(MESSAGES) == GOOD
    assert llm.last_call.provider == "groq"
    assert llm.last_call.attempts == 3                     # 2 on mistral, 1 on groq
    assert all("invalid" in e for e in llm.last_call.errors)


def test_permanent_error_skips_the_provider_at_once() -> None:
    mistral = FakeLLM(HTTPError(401))  # wrong API key
    llm = make(("mistral", mistral), ("groq", FakeLLM(GOOD)))

    assert llm.invoke(MESSAGES) == GOOD
    assert len(mistral.calls) == 1                         # not retried: it would fail again
    assert llm.waits == []


def test_all_providers_failing_raises_one_clear_error() -> None:
    llm = make(("mistral", FakeLLM(TimeoutError())), ("groq", FakeLLM(HTTPError(503))))

    with pytest.raises(AllProvidersFailed) as failure:
        llm.invoke(MESSAGES)

    assert len(failure.value.errors) == 4                  # 2 attempts x 2 providers, all recorded
    assert llm.last_call.provider is None