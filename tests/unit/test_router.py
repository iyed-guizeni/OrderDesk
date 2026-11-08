from orderdesk.domain.classification import EmailClassification
from orderdesk.domain.email import EmailContext
from orderdesk.workflow.router import route_email
from orderdesk.workflow.state import WorkflowState


def make_state(**classification_changes: object) -> WorkflowState:
    classification_data: dict[str, object] = {
        "evidence": "Order request",
        "label": "new_order",
        "confidence": "high",
    }
    classification_data.update(classification_changes)
    classification = EmailClassification(**classification_data)
    email = EmailContext(
        tenant_id=1,
        email_id=1,
        sender="customer@example.com",
        subject="Order",
        body="Please send 10 units.",
        is_reply=False,
    )
    return WorkflowState(email=email, classification=classification)


def test_low_confidence_routes_to_triage() -> None:
    assert route_email(make_state(confidence="low")) == "triage"


def test_mixed_intent_routes_to_triage() -> None:
    assert route_email(make_state(also_contains=["amendment"])) == "triage"


def test_clear_label_routes_to_matching_branch() -> None:
    assert route_email(make_state(label="cancellation")) == "cancellation"
