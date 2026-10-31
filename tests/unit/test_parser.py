import hashlib
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

import pytest

from orderdesk.intake.parser import EmailParseError, html_to_text, parse_email

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "emails"


def read_fixture(name: str) -> bytes:
    path = FIXTURES / name
    if not path.exists():
        pytest.fail(f"{path} is missing: run `python scripts/make_sample_emails.py` first")
    return path.read_bytes()


def test_karim_order_headers() -> None:
    email = parse_email(read_fixture("01_karim_order_BM-2291.eml"))

    assert email.message_id == "<20261021091402.4f7a@btp-martin.fr>"
    assert email.message_id_is_synthetic is False
    assert email.from_address == "karim@btp-martin.fr"
    assert email.from_name == "Karim Benali"
    assert email.reply_to == "achats@btp-martin.fr"
    assert email.to_addresses == ["orders@dupont-fixations.fr"]
    assert email.cc_addresses == ["compta@btp-martin.fr"]
    assert email.subject == "Commande BM-2291 - livraison jeudi"
    assert email.in_reply_to is None
    assert email.references_ids == []


def test_sent_at_is_timezone_aware() -> None:
    email = parse_email(read_fixture("01_karim_order_BM-2291.eml"))

    # 09:14:02 at +02:00 is 07:14:02 UTC
    assert email.sent_at == datetime(2026, 10, 21, 7, 14, 2, tzinfo=UTC)


def test_karim_order_body_and_attachment() -> None:
    email = parse_email(read_fixture("01_karim_order_BM-2291.eml"))

    assert email.body_text is not None
    assert "chantier de Villeurbanne" in email.body_text

    assert len(email.attachments) == 1
    attachment = email.attachments[0]
    assert attachment.filename == "BC_BM-2291.csv"
    assert attachment.content_type == "text/csv"
    assert b"rondelles M8" in attachment.data
    assert attachment.size_bytes == len(attachment.data)
    assert attachment.sha256 == hashlib.sha256(attachment.data).hexdigest()


def test_order_in_body_without_attachment() -> None:
    email = parse_email(read_fixture("02_garage_dubois_order_no_po.eml"))

    assert email.from_name == "Garage Dubois"
    assert email.attachments == []
    assert email.body_text is not None
    assert "100 ecrous M10" in email.body_text


def test_reply_has_thread_headers() -> None:
    email = parse_email(read_fixture("03_karim_correction_reply.eml"))

    assert email.in_reply_to == "<20261021093010.77c1@dupont-fixations.fr>"
    assert email.references_ids == [
        "<20261021091402.4f7a@btp-martin.fr>",
        "<20261021093010.77c1@dupont-fixations.fr>",
    ]


def test_exact_duplicate_has_same_id_and_hash() -> None:
    original = parse_email(read_fixture("01_karim_order_BM-2291.eml"))
    duplicate = parse_email(read_fixture("04_karim_order_duplicate.eml"))

    assert duplicate.message_id == original.message_id
    assert duplicate.raw_sha256 == original.raw_sha256


def _simple_email(**headers: str) -> bytes:
    msg = EmailMessage()
    for name, value in headers.items():
        msg[name.replace("_", "-")] = value
    msg.set_content("Hello")
    return msg.as_bytes()


def test_missing_message_id_gets_a_stable_synthetic_id() -> None:
    raw = _simple_email(From="a@example.com", Subject="no id")

    first = parse_email(raw)
    second = parse_email(raw)

    assert first.message_id_is_synthetic is True
    assert first.message_id.endswith("@orderdesk.synthetic>")
    assert first.message_id == second.message_id  # same content -> same id


def test_email_without_sender_is_rejected() -> None:
    raw = _simple_email(Subject="who sent this?")

    with pytest.raises(EmailParseError):
        parse_email(raw)


def test_html_only_body_is_converted_to_text() -> None:
    msg = EmailMessage()
    msg["From"] = "shop@example.com"
    msg["Message-ID"] = "<html-only@example.com>"
    msg.set_content(
        "<html><style>p {color: red}</style><body><p>Please send:</p><p>200 washers M8</p></body></html>",
        subtype="html",
    )

    email = parse_email(msg.as_bytes())

    assert email.body_text == "Please send:\n200 washers M8"


def test_html_to_text_drops_scripts() -> None:
    assert html_to_text("<p>Hi</p><script>alert(1)</script><p>there</p>") == "Hi\nthere"