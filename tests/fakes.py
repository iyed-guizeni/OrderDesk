
from collections.abc import Sequence
from typing import Any


class FakeLLM:
    def __init__(self, *answers: Any) -> None:
        if not answers:
            raise ValueError("give FakeLLM at least one answer")
        self._answers = list(answers)
        self.calls: list[Sequence[Any]] = []
    
    def invoke(self, messages: Sequence[Any]) -> Any:
        self.calls.append(messages)
        answer = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def last_messages(self) -> Sequence[Any]:
        """The messages of the most recent call (most tests only need this)."""
        if not self.calls:
            raise AssertionError("FakeLLM was never called")
        return self.calls[-1]