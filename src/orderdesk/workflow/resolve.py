from collections.abc import Callable

from orderdesk.catalogue.ports import AliasStore, ErpPort, ProductSearch
from orderdesk.resolve.ladder import resolve_order
from orderdesk.workflow.state import WorkflowState


def make_resolve_node(erp: ErpPort, search: ProductSearch, aliases: AliasStore) -> Callable[[WorkflowState], dict]:
    def resolve(state: WorkflowState) -> dict:
        if state.extracted_order is None:
            raise ValueError("cannot resolve an order before extraction")
        return {"resolved_order": resolve_order(state.extracted_order, state.email.sender, erp, search, aliases)}

    return resolve
