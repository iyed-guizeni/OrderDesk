"""Tests for converting approved drafts and writing idempotent ERP quotations."""

import pytest

from orderdesk.catalogue.memory import MemoryCatalogue
from orderdesk.checks.order import to_quotation_request
from orderdesk.domain.catalogue import Product
from orderdesk.domain.draft import DraftLine, DraftOrder
from orderdesk.workflow.state import WorkflowState
from orderdesk.workflow.write_draft import make_write_draft_node
from tests.factories import make_email


def _draft(*, customer_id: int | None = 2, product: Product | None = None) -> DraftOrder:
    line = DraftLine(
        line_index=0,
        raw_text="2 taps",
        quantity=2,
        unit="piece",
        product=product,
        unit_price=175.0 if product is not None else None,
        line_total=350.0 if product is not None else None,
    )
    return DraftOrder(customer_id=customer_id, po_number="PO-4471", lines=[line], total=350.0)


def test_to_quotation_request_keeps_values_and_origin() -> None:
    product = Product(code="PF60375", name="Tap", family="Tap", list_price=175.0)

    request = to_quotation_request(_draft(product=product), 999, 42)

    assert request.origin == "OrderDesk 999-42"
    assert request.client_ref == "PO-4471"
    assert [(line.code, line.quantity, line.unit_price) for line in request.lines] == [("PF60375", 2.0, 175.0)]


def test_to_quotation_request_rejects_missing_customer_or_product() -> None:
    with pytest.raises(ValueError, match="customer"):
        to_quotation_request(_draft(customer_id=None), 999, 42)
    with pytest.raises(ValueError, match="no product"):
        to_quotation_request(_draft(product=None), 999, 42)


def test_node_writes_one_quotation_and_returns_id() -> None:
    catalogue = MemoryCatalogue([])
    product = Product(code="PF60375", name="Tap", family="Tap", list_price=175.0)
    state = WorkflowState(email=make_email(), approved_order=_draft(product=product))

    update = make_write_draft_node(catalogue)(state)

    assert update == {"erp_quotation_id": "DRAFT-0001"}
    assert len(catalogue.draft_quotations) == 1


def test_node_is_idempotent() -> None:
    catalogue = MemoryCatalogue([])
    product = Product(code="PF60375", name="Tap", family="Tap", list_price=175.0)
    state = WorkflowState(email=make_email(), approved_order=_draft(product=product))
    node = make_write_draft_node(catalogue)

    first = node(state)
    second = node(state)

    assert first == second
    assert len(catalogue.draft_quotations) == 1


def test_node_refuses_no_approved_order() -> None:
    with pytest.raises(ValueError, match="approved order"):
        make_write_draft_node(MemoryCatalogue([]))(WorkflowState(email=make_email()))
