"""The ExtractedOrder model itself (no LLM)."""

import datetime

import ormsgpack
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer, _msgpack_ext_hook_to_json

from orderdesk.domain.extraction import ExtractedOrder, RequestedDate


def test_a_date_survives_the_studio_state_view() -> None:
    """Studio shows the checkpoint as JSON (a date becomes [2026, 11, 20]) and sends it back on resume."""
    order = ExtractedOrder(requested_date=RequestedDate(text="vendredi 20", value=datetime.date(2026, 11, 20)))
    _, data = JsonPlusSerializer().dumps_typed(order.model_dump())
    as_studio_sends_it = ormsgpack.unpackb(data, ext_hook=_msgpack_ext_hook_to_json)

    assert ExtractedOrder.model_validate(as_studio_sends_it) == order


def test_the_model_still_sees_a_plain_date_field() -> None:
    schema = RequestedDate.model_json_schema()["properties"]["value"]

    assert {"type": "string", "format": "date"} in schema["anyOf"]


def test_the_word_none_written_by_the_model_is_no_value() -> None:
    """Some models write "None" for an empty PO: grounding then flagged po_not_in_source 'None'."""
    order = ExtractedOrder.model_validate({
        "po_number": "None",
        "lines": [{"raw_text": "2 mitigeurs", "product": {"kind": "named", "text": "mitigeurs", "code_as_written": "null",
                                                          "variant_text": " N/A "}}],
    })

    assert order.po_number is None
    assert (order.lines[0].product.code_as_written, order.lines[0].product.variant_text) == (None, None)
    assert ExtractedOrder(po_number="4471").po_number == "4471"


def test_the_word_none_for_a_whole_section_is_no_value() -> None:
    """Seen in the e2e eval: delivery 'none' and requested_date 'none' failed validation and cost a retry."""
    order = ExtractedOrder.model_validate({"delivery": "none", "requested_date": "None", "order_reference": "null"})

    assert (order.delivery, order.requested_date, order.order_reference) == (None, None, None)
