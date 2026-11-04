"""Unit tests for reading attachments and the excerpt given to the classifier."""

import io

from orderdesk.documents.excerpt import MAX_EXCERPT_CHARS, add_attachment_excerpt
from orderdesk.documents.text import extract_text
from orderdesk.domain.email import AttachmentRef, EmailContext
from orderdesk.prompts.classification import build_user_prompt


class FakeStorage:
    """Storage in memory: key -> bytes."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def get(self, key: str) -> bytes:
        return self.files[key]


def make_pdf(text: str) -> bytes:
    """A real one-page PDF with some text (reportlab is a dev dependency)."""
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawString(72, 750, text)
    pdf.save()
    return buffer.getvalue()


def make_email(body: str, attachments: list[AttachmentRef]) -> EmailContext:
    return EmailContext(tenant_id=999, email_id=1, sender="a@b.example", subject="Commande", body=body,
                        is_reply=False, attachments=attachments)


PDF_REF = AttachmentRef(filename="scan_0042.pdf", content_type="application/pdf", storage_key="k/pdf")


# ---------- extract_text ----------

def test_pdf_text_is_extracted() -> None:
    text = extract_text(make_pdf("BON DE COMMANDE 5133 - PF60375 Qte 2"), "application/pdf", "bc.pdf")
    assert text is not None and "BON DE COMMANDE 5133" in text


def test_french_csv_with_semicolons_and_old_encoding() -> None:
    data = "Réf;Désignation;Qté\nPF60375;Mitigeur Profilo;2\n".encode("cp1252")
    assert extract_text(data, "text/csv", "commande.csv") == "Réf | Désignation | Qté\nPF60375 | Mitigeur Profilo | 2"


def test_images_and_broken_files_give_none() -> None:
    assert extract_text(b"\xff\xd8\xff", "image/jpeg", "photo_fuite.jpg") is None
    assert extract_text(b"not really a pdf", "application/pdf", "broken.pdf") is None  # no crash


# ---------- add_attachment_excerpt ----------

def test_short_body_gets_the_attachment_text() -> None:
    storage = FakeStorage({"k/pdf": make_pdf("BON DE COMMANDE 5133")})

    email = add_attachment_excerpt(make_email("", [PDF_REF]), storage)

    assert email.attachment_excerpt is not None
    assert email.attachment_excerpt.startswith("[scan_0042.pdf]")
    assert "BON DE COMMANDE 5133" in email.attachment_excerpt


def test_long_body_is_left_alone() -> None:
    storage = FakeStorage({"k/pdf": make_pdf("BON DE COMMANDE 5133")})
    body = "Bonjour, " + "merci de nous envoyer les produits suivants pour le showroom " * 3

    email = add_attachment_excerpt(make_email(body, [PDF_REF]), storage)

    assert email.attachment_excerpt is None


def test_excerpt_is_cut() -> None:
    txt = AttachmentRef(filename="long.txt", content_type="text/plain", storage_key="k/txt")
    storage = FakeStorage({"k/txt": ("x" * 10_000).encode()})

    email = add_attachment_excerpt(make_email("", [txt]), storage)

    assert email.attachment_excerpt is not None
    assert len(email.attachment_excerpt) <= len("[long.txt]\n") + MAX_EXCERPT_CHARS


# ---------- prompt ----------

def test_prompt_shows_the_excerpt_only_when_present() -> None:
    excerpt = "[scan_0042.pdf]\nBON DE COMMANDE"
    with_excerpt = make_email("", [PDF_REF]).model_copy(update={"attachment_excerpt": excerpt})

    assert "<attachment>\n[scan_0042.pdf]\nBON DE COMMANDE\n</attachment>" in build_user_prompt(with_excerpt)
    assert "<attachment>" not in build_user_prompt(make_email("Bonjour", []))