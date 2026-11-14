"""Grounding checks: what Extract wrote must be findable in what the client sent."""

import pytest

from orderdesk.checks.grounding import check_grounding
from orderdesk.domain.extraction import ExtractedLine, ExtractedOrder, ProductInfo

SUBJECT = "Commande BC-7781"
BODY = "Bonjour,\n• 1,200 x washer kit WK-12\n3 mitigeurs Profilo noir brossé\nMerci"


def make_line(raw_text: str, quantity: float | None = None, code: str | None = None) -> ExtractedLine:
    return ExtractedLine(raw_text=raw_text, quantity=quantity,
                         product=ProductInfo(kind="named", code_as_written=code))


def issues_for(*lines: ExtractedLine, body: str = BODY, attachments: tuple[str, ...] = (), po: str | None = None):
    order = ExtractedOrder(po_number=po, lines=list(lines))
    return [(issue.kind, issue.line_index) for issue in check_grounding(order, SUBJECT, body, attachments)]


# ---------- lines ----------


def test_a_line_copied_from_the_email_is_grounded() -> None:
    assert issues_for(make_line("3 mitigeurs Profilo noir brossé", 3)) == []


def test_case_accents_bullets_and_punctuation_do_not_matter() -> None:
    assert issues_for(make_line("1,200 x Washer-Kit WK-12", 1200, "WK-12"), make_line("3 MITIGEURS profilo noir brosse", 3)) == []


def test_a_line_found_only_in_an_attachment_is_grounded() -> None:
    line = make_line("PF60375 Mitigeur lavabo chrome Qte 2", 2)

    assert issues_for(line, attachments=("BON DE COMMANDE 5133\nPF60375 Mitigeur lavabo chrome Qte 2",)) == []


def test_an_invented_line_is_flagged() -> None:
    assert issues_for(make_line("2 colonnes de douche chrome", 2)) == [("line_not_in_source", 0)]


def test_a_line_taken_from_a_quoted_message_is_flagged_as_such() -> None:
    body = "Merci d'ajouter 2 douchettes noir mat.\n\n> Nous confirmons votre commande :\n> - 4 x PF60375 Mitigeur lavabo chrome"

    assert issues_for(make_line("4 x PF60375 Mitigeur lavabo chrome", 4, "PF60375"), body=body) == [
        ("line_only_in_quoted_text", 0)
    ]


# ---------- quantities ----------


@pytest.mark.parametrize(
    ("raw_text", "quantity"),
    [
        ("1 000 joints toriques", 1000),
        ("1.000 joints", 1000),
        ("1,000 O-rings", 1000),
        ("1,5 m de flexible", 1.5),
        ("2.5 m chrome hose", 2.5),
        ("PF60375 x 10", 10),
        ("une douzaine de cartouches", 12),
        ("a dozen wall brackets", 12),
        ("une demi-douzaine de douchettes", 6),
        ("deux inverseurs 3 voies", 2),
    ],
)
def test_quantities_written_in_many_ways_are_found(raw_text: str, quantity: float) -> None:
    assert issues_for(make_line(raw_text, quantity), body=raw_text) == []


def test_a_quantity_not_written_in_the_line_is_flagged() -> None:
    assert issues_for(make_line("3 mitigeurs Profilo noir brossé", 30)) == [("quantity_not_in_line", 0)]


def test_a_line_without_quantity_is_not_checked_for_one() -> None:
    assert issues_for(make_line("3 mitigeurs Profilo noir brossé", None)) == []


# ---------- codes and PO ----------


def test_a_code_written_with_other_spacing_is_found() -> None:
    assert issues_for(make_line("1,200 x washer kit WK-12", 1200, "wk 12")) == []


def test_an_invented_code_is_flagged() -> None:
    assert issues_for(make_line("3 mitigeurs Profilo noir brossé", 3, "PF60376")) == [("code_not_in_source", 0)]


def test_a_po_number_in_the_subject_is_found() -> None:
    assert issues_for(po="7781") == []


def test_an_invented_po_number_is_flagged() -> None:
    assert issues_for(po="4471") == [("po_not_in_source", None)]
