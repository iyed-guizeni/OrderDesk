"""The deterministic resolve ladder: which product is each extracted line? No LLM here.

For each line, cheapest and surest first:
  1. alias       this customer already wrote these words and a person approved the product
  2. exact code  the client wrote a reference that exists in the ERP
  3. search      one candidate is clearly better than the others
When none decides, the line stays unresolved WITH A REASON, and next_step() routes it:
the agent when the answer may be in our data (past orders, catalogue), a person otherwise.
"""

from orderdesk.catalogue.ports import AliasStore, ErpPort, ProductSearch
from orderdesk.domain.catalogue import Candidate, Customer
from orderdesk.domain.extraction import ExtractedLine, ExtractedOrder
from orderdesk.domain.resolution import LineResolution, NextStep, Reason, ResolvedOrder

MAX_CANDIDATES = 5
# Tuned to the search score scale (share of the client's words found). Re-check them when the search changes.
MIN_SCORE = 0.5  # below this, a candidate is not a real match
MIN_GAP = 0.15  # the best candidate must beat the second by this much to be a clear winner

AGENT_REASONS: set[Reason] = {"no_candidates"}  # the agent can try other wordings in the catalogue
AGENT_REASONS_WITH_CUSTOMER: set[Reason] = {"previous_order", "variant_missing"}  # need this customer's history


def next_step(reason: Reason, customer_known: bool) -> NextStep:
    """Who continues with an unresolved line. "ambiguous" goes to a person until the LLM choice step exists."""
    if reason in AGENT_REASONS or (reason in AGENT_REASONS_WITH_CUSTOMER and customer_known):
        return "agent"
    return "human"


def _unresolved(index: int, reason: Reason, customer: Customer | None, candidates=()) -> LineResolution:
    return LineResolution(line_index=index, status="unresolved", reason=reason, candidates=list(candidates),
                          next_step=next_step(reason, customer is not None))


def _words(line: ExtractedLine) -> str:
    return " ".join(part for part in (line.product.text, line.product.variant_text) if part)


def resolve_line(
    index: int, line: ExtractedLine, customer: Customer | None, erp: ErpPort, search: ProductSearch,
    aliases: AliasStore,
) -> LineResolution:
    product = line.product
    if product.kind == "missing":
        return _unresolved(index, "product_missing", customer)
    if product.kind == "previous_order":
        return _unresolved(index, "previous_order", customer)

    words = _words(line)
    if customer is not None and words:
        code = aliases.find_alias(customer.id, words)
        found = erp.get_product(code) if code else None
        if found is not None:  # an alias to a product deleted from the ERP is ignored
            return LineResolution(line_index=index, status="resolved", product=found, method="alias",
                                  next_step="done")

    if product.code_as_written:
        found = erp.get_product(product.code_as_written)
        if found is not None:
            return LineResolution(line_index=index, status="resolved", product=found, method="exact_code",
                                  next_step="done")

    query = " ".join(part for part in (product.code_as_written, words) if part)
    if not query:
        return _unresolved(index, "product_missing", customer)
    candidates = sorted(search.search(query, MAX_CANDIDATES), key=lambda c: -c.score)
    candidates = [c for c in candidates if c.score >= MIN_SCORE]
    if not candidates:
        return _unresolved(index, "no_candidates", customer)

    close = [c for c in candidates if candidates[0].score - c.score < MIN_GAP]
    if len(close) == 1:
        return LineResolution(line_index=index, status="resolved", product=close[0].product, method="search",
                              candidates=candidates, next_step="done")
    return _unresolved(index, _why_close(close, line), customer, candidates)


def _why_close(close: list[Candidate], line: ExtractedLine) -> Reason:
    """Several close candidates: the same product in different finishes, or really different products?"""
    same_family = len({c.product.family for c in close}) == 1
    return "variant_missing" if same_family and not line.product.variant_text else "ambiguous"


def resolve_order(
    order: ExtractedOrder, sender: str, erp: ErpPort, search: ProductSearch, aliases: AliasStore
) -> ResolvedOrder:
    customer = erp.find_customer(sender)
    lines = [resolve_line(i, line, customer, erp, search, aliases) for i, line in enumerate(order.lines)]
    return ResolvedOrder(customer=customer, lines=lines)
