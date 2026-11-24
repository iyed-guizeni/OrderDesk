"""Code checks on the Resolver agent's answer. "AI interprets, code verifies": a proposal that fails
any check is rejected and the line goes to a person. The agent's evidence is never trusted on its own.

Most rules mirror a rule of the prompt (prompts/resolver_agent.py): the prompt asks, the code makes sure.
"""

from orderdesk.catalogue.ports import ErpPort
from orderdesk.catalogue.text import code_key
from orderdesk.domain.catalogue import Customer, Product
from orderdesk.domain.extraction import ExtractedLine
from orderdesk.domain.resolution import AgentProposal, LineResolution

NONE = "NONE"


def check_proposal(
    proposal: AgentProposal, line: ExtractedLine, resolution: LineResolution, erp: ErpPort, customer: Customer | None
) -> tuple[Product | None, str | None]:
    """(the verified product, None) if the proposal holds; (None, why it was rejected) otherwise."""
    code = proposal.code.strip()
    if code.upper() == NONE:
        return None, "the agent found nothing"

    product = erp.get_product(code)
    if product is None:
        return None, f"code {code!r} does not exist"

    from_past_order = proposal.source == "past_order"
    if from_past_order:
        if customer is None or not proposal.source_order_id:
            return None, "a past order is cited, but there is no customer or no order id"
        order = erp.get_order(customer.id, proposal.source_order_id)
        if order is None:
            return None, f"order {proposal.source_order_id!r} is not a confirmed order of this customer"
        order_codes = {order_line.code for order_line in order.lines}
        if product.code not in order_codes:
            return None, f"order {order.order_id} does not contain {product.code}"
        if resolution.reason == "previous_order" and not line.product.text and len(order_codes) > 1:
            return None, f"the client names no product and order {order.order_id} has {len(order_codes)}"

    if resolution.reason == "previous_order" and not from_past_order:
        return None, "'same as last time' must be answered from a past order"

    if resolution.reason == "variant_missing":
        if product.code not in {c.product.code for c in resolution.candidates}:
            return None, f"{product.code} is not one of the finishes found by the search"
        if not from_past_order:
            return None, "a finish the client did not write must come from this customer's past orders"

    written = line.product.code_as_written
    if written and code_key(written) != code_key(product.code) and not from_past_order:
        return None, f"the client wrote {written!r}: a different code needs a past order as evidence"

    return product, None
