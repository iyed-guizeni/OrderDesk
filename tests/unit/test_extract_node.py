"""Unit tests for the Extract node and for reading attachments. No network: FakeLLM + FakeStorage."""

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from orderdesk.documents.reading import read_attachments
from orderdesk.domain.extraction import ExtractedLine, ExtractedOrder, ProductInfo
from orderdesk.workflow.new_order import make_extract_node
from orderdesk.workflow.state import WorkflowState
from tests.factories import make_attachment, make_email, make_pdf
from tests.fakes import FakeLLM, FakeStorage

PROFILE = "ACME Robinetterie sells taps and shower systems.\nFinishes: chrome, noir brossé."
PDF_TEXT = "BON DE COMMANDE 5133 - PF60375 Qte 2"

PDF = make_attachment("bc.pdf", "application/pdf")
PHOTO = make_attachment("photo.jpg", "image/jpeg")


def make_storage() -> FakeStorage:
    return FakeStorage({PDF.storage_key: make_pdf(PDF_TEXT), PHOTO.storage_key: b"\xff\xd8\xff\xe0 jpeg bytes"})


def make_answer() -> ExtractedOrder:
    line = ExtractedLine(raw_text="PF60375 Qte 2", quantity=2, product=ProductInfo(kind="named", code_as_written="PF60375"))
    return ExtractedOrder(po_number="5133", lines=[line])


def run_node(llm: FakeLLM, email=None) -> dict:
    node = make_extract_node(llm, PROFILE, make_storage())
    return node(WorkflowState(email=email or make_email(attachments=[PDF, PHOTO])))


def sent_messages() -> tuple[SystemMessage, HumanMessage]:
    """Run the node once and return the (system, user) messages it sent to the LLM."""
    llm = FakeLLM(make_answer())
    run_node(llm)
    system, user = llm.last_messages
    return system, user


def lines_between(text: str, start: str, end: str) -> list[str]:
    """The whole lines strictly between the line `start` and the next line `end`."""
    lines = text.splitlines()
    first = lines.index(start) + 1
    return lines[first : lines.index(end, first)]


# ---------- read_attachments ----------


def test_read_attachments_splits_readable_and_unreadable_files() -> None:
    email = make_email(attachments=[PDF, PHOTO])

    readable, unreadable = read_attachments(email, make_storage())

    assert readable == [("bc.pdf", PDF_TEXT)]
    assert unreadable == ["photo.jpg"]


# ---------- the node ----------


def test_node_returns_only_the_extracted_order() -> None:
    answer = make_answer()

    update = run_node(FakeLLM(answer))

    assert update == {"extracted_order": answer}


def test_node_fails_clearly_when_the_llm_gives_no_answer() -> None:
    with pytest.raises(ValueError, match="no valid ExtractedOrder"):
        run_node(FakeLLM(None))  # e.g. the model did not call the tool


def test_provider_errors_are_not_hidden() -> None:
    with pytest.raises(TimeoutError):  # the job queue retries it later
        run_node(FakeLLM(TimeoutError("provider too slow")))


# ---------- what the node sends ----------


def test_user_prompt_gives_the_email_date_with_weekday() -> None:
    _, user = sent_messages()

    assert "Email date: Friday 2026-11-06" in user.content.splitlines()


def test_user_prompt_puts_the_body_inside_email_tags() -> None:
    _, user = sent_messages()

    email_lines = lines_between(user.content, "<email>", "</email>")
    assert email_lines == [
        "Subject: Bon de commande 4471",
        "",
        "Bonjour,",
        "2 mitigeurs Profilo noir brossé, livraison avant vendredi.",
        "Merci",
    ]


def test_user_prompt_puts_the_pdf_text_inside_its_attachment_tag() -> None:
    _, user = sent_messages()

    assert lines_between(user.content, '<attachment name="bc.pdf">', "</attachment>") == [PDF_TEXT]


def test_user_prompt_lists_unreadable_attachments() -> None:
    _, user = sent_messages()

    assert "Attachments without readable text: photo.jpg" in user.content.splitlines()


def test_system_prompt_contains_the_profile_inside_business_tags() -> None:
    system, _ = sent_messages()

    assert lines_between(system.content, "<business>", "</business>") == PROFILE.splitlines()
