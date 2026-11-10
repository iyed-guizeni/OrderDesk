
import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from orderdesk.domain.classification import EmailClassification
from orderdesk.domain.email import AttachmentRef, EmailContext
from orderdesk.prompts.classification import MAX_BODY_CHARS, build_system_prompt, build_user_prompt
from orderdesk.workflow.classification import make_classify_node
from orderdesk.workflow.state import WorkflowState
from tests import factories
from tests.fakes import FakeLLM

STORAGE_KEY = "tenants/999/attachments/3b/3b7e0c"


PROFILE = "ACME Robinetterie sells taps and shower systems."


def make_email(**changes) -> EmailContext:
    """The shared order email, with one attached PDF; each test changes only what it is about."""
    attachment = AttachmentRef(filename="BC_4471.pdf", content_type="application/pdf", storage_key=STORAGE_KEY)
    fields = {
        "body": "Ci-joint notre bon de commande 4471 : 2 mitigeurs Profilo 3 sorties noir brossé.",
        "attachments": [attachment],
    }
    return factories.make_email(**{**fields, **changes})


def make_state(**changes) -> WorkflowState:
    return WorkflowState(email=make_email(**changes))


def make_answer(**changes) -> EmailClassification:
    fields = { "evidence": "Ci-joint notre bon de commande 4471", "label": "new_order",
              "confidence": "high"}
    return EmailClassification(**{**fields, **changes})



def test_node_returns_only_the_classification() -> None:
    answer = make_answer()
    node = make_classify_node(FakeLLM(answer), PROFILE)

    update = node(make_state())

    assert update == {"classification": answer}  


def test_node_sends_system_prompt_then_the_email() -> None:
    llm = FakeLLM(make_answer())
    node = make_classify_node(llm, PROFILE)

    node(make_state())

    system, user = llm.last_messages
    assert isinstance(system, SystemMessage) and system.content == build_system_prompt(PROFILE)
    assert isinstance(user, HumanMessage)
    assert "Subject: Bon de commande 4471" in user.content
    assert "2 mitigeurs Profilo" in user.content


def test_node_calls_the_llm_once() -> None:
    llm = FakeLLM(make_answer())

    make_classify_node(llm, PROFILE)(make_state())

    assert len(llm.calls) == 1


def test_node_fails_clearly_when_the_llm_gives_no_answer() -> None:
    node = make_classify_node(FakeLLM(None), PROFILE)  # e.g. the model did not call the tool

    with pytest.raises(ValueError, match="no valid EmailClassification"):
        node(make_state())


def test_provider_errors_are_not_hidden() -> None:
    node = make_classify_node(FakeLLM(TimeoutError("provider too slow")), PROFILE)

    with pytest.raises(TimeoutError):  # the job queue will retry it later
        node(make_state())


#  the user prompt 

def test_prompt_puts_the_email_inside_tags() -> None:
    prompt = build_user_prompt(make_email())

    email_part = prompt.split("<email>")[1].split("</email>")[0]
    assert "Subject: Bon de commande 4471" in email_part
    assert "2 mitigeurs Profilo" in email_part


def test_prompt_shows_reply_as_yes_or_no() -> None:
    assert "Reply to an earlier email: yes" in build_user_prompt(make_email(is_reply=True))
    assert "Reply to an earlier email: no" in build_user_prompt(make_email(is_reply=False))


def test_prompt_lists_attachment_names_but_never_storage_keys() -> None:
    prompt = build_user_prompt(make_email())

    assert "Attachments: BC_4471.pdf" in prompt
    assert STORAGE_KEY not in prompt          # internal paths must not reach an external provider
    assert "AttachmentRef(" not in prompt     # no Python repr in the prompt


def test_prompt_handles_empty_values() -> None:
    prompt = build_user_prompt(make_email(subject="", attachments=[]))

    assert "Subject: (no subject)" in prompt
    assert "Attachments: none" in prompt


def test_prompt_cuts_very_long_bodies() -> None:
    prompt = build_user_prompt(make_email(body="x" * (MAX_BODY_CHARS + 5000)))

    assert "x" * MAX_BODY_CHARS in prompt
    assert "x" * (MAX_BODY_CHARS + 1) not in prompt