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
