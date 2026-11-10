from orderdesk.domain.classification import EmailClassification
from orderdesk.workflow.router import route_email
from orderdesk.workflow.state import WorkflowState
from tests.factories import make_email


def make_state(**classification_changes: object) -> WorkflowState:
    classification_data: dict[str, object] = {
        "evidence": "Order request",
        "label": "new_order",
        "confidence": "high",
    }
    classification_data.update(classification_changes)
    classification = EmailClassification(**classification_data)
    return WorkflowState(email=make_email(), classification=classification)


def test_low_confidence_routes_to_triage() -> None:
    assert route_email(make_state(confidence="low")) == "triage"


def test_mixed_intent_routes_to_triage() -> None:
    assert route_email(make_state(also_contains=["amendment"])) == "triage"


def test_clear_label_routes_to_matching_branch() -> None:
    assert route_email(make_state(label="cancellation")) == "cancellation"
