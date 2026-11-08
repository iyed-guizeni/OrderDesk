from orderdesk.workflow.state import WorkflowState


def route_email(state: WorkflowState) -> str:
    """Choose the next workflow branch after classification."""
    if state.classification is None:
        raise ValueError("cannot route an email before classification")

    if state.classification.confidence == "low" or state.classification.also_contains:
        return 'triage'
    return state.classification.label