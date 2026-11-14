"""Code checks on the Resolver agent's answer. "AI interprets, code verifies": a proposal that fails
any check is rejected and the line goes to a person. The agent's evidence is never trusted on its own.
"""

from orderdesk.catalogue.ports import ErpPort
from orderdesk.domain.catalogue import Customer, Product
from orderdesk.domain.resolution import AgentProposal, LineResolution

NONE = "NONE"


def check_proposal(
    proposal: AgentProposal, resolution: LineResolution, erp: ErpPort, customer: Customer | None
) -> tuple[Product | None, str | None]:
    """(the verified product, None) if the proposal holds; (None, why it was rejected) otherwise."""
    code = proposal.code.strip()
    if code.upper() == NONE:
        return None, "the agent found nothing"

    product = erp.get_product(code)
    if product is None:
        return None, f"code {code!r} does not exist"

    if proposal.source == "past_order":
        if customer is None or not proposal.source_order_id:
            return None, "a past order is cited, but there is no customer or no order id"
        order = erp.get_order(customer.id, proposal.source_order_id)
        if order is None:
            return None, f"order {proposal.source_order_id!r} is not a confirmed order of this customer"
        if product.code not in {line.code for line in order.lines}:
            return None, f"order {order.order_id} does not contain {product.code}"

    if resolution.reason == "previous_order" and proposal.source != "past_order":
        return None, "'same as last time' must be answered from a past order"

    if resolution.reason == "variant_missing" and product.code not in {c.product.code for c in resolution.candidates}:
        return None, f"{product.code} is not one of the finishes found by the search"

    return product, None
