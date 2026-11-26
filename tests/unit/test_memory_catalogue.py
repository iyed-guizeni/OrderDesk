"""Tests for in-memory ERP drafts and learned product aliases."""

import pytest
from pydantic import ValidationError

from orderdesk.catalogue.memory import MemoryCatalogue
from orderdesk.domain.quotation import QuotationLine, QuotationRequest, orderdesk_origin


def test_orderdesk_origin() -> None:
    assert orderdesk_origin(999, 42) == "OrderDesk 999-42"


def test_same_origin_is_idempotent() -> None:
    catalogue = MemoryCatalogue([])
    request = QuotationRequest(
        origin="OrderDesk 999-42", customer_id=2, client_ref="PO-1", lines=[QuotationLine(code="P1", quantity=2)]
    )

    first = catalogue.create_draft_quotation(request)
    second = catalogue.create_draft_quotation(request)

    assert first == second
    assert len(catalogue.draft_quotations) == 1


def test_different_origins_get_different_ids() -> None:
    catalogue = MemoryCatalogue([])
    first = catalogue.create_draft_quotation(QuotationRequest(
        origin="OrderDesk 999-42", customer_id=2, client_ref=None,
        lines=[QuotationLine(code="P1", quantity=1)],
    ))
    second = catalogue.create_draft_quotation(QuotationRequest(
        origin="OrderDesk 999-43", customer_id=2, client_ref=None,
        lines=[QuotationLine(code="P1", quantity=1)],
    ))

    assert first != second


def test_draft_never_appears_in_confirmed_orders() -> None:
    catalogue = MemoryCatalogue([])
    catalogue.create_draft_quotation(QuotationRequest(
        origin="OrderDesk 999-42", customer_id=2, client_ref=None,
        lines=[QuotationLine(code="P1", quantity=1)],
    ))

    assert catalogue.past_orders(2, 10) == []
    assert catalogue.get_order(2, "DRAFT-0001") is None


def test_learn_alias_is_normalized_and_customer_scoped() -> None:
    catalogue = MemoryCatalogue([])

    outcome = catalogue.learn_alias(2, "Robinet  Profilo NOIR", "PF60376")

    assert outcome == "learned"
    assert catalogue.find_alias(2, "robinet profilo noir") == "PF60376"
    assert catalogue.find_alias(1, "robinet profilo noir") is None


def test_alias_conflict_forgets_and_learning_outcomes() -> None:
    catalogue = MemoryCatalogue([])

    assert catalogue.learn_alias(2, "robinet noir", "PF60376") == "learned"
    assert catalogue.learn_alias(2, "robinet noir", "PF60376") == "unchanged"
    assert catalogue.learn_alias(2, "robinet noir", "PF60375") == "forgotten"
    assert catalogue.find_alias(2, "robinet noir") is None


def test_quotation_models_reject_invalid_inputs() -> None:
    with pytest.raises(ValidationError):
        QuotationRequest(origin="OrderDesk 999-42", customer_id=2, client_ref=None, lines=[])
    with pytest.raises(ValidationError):
        QuotationLine(code="P1", quantity=0)
