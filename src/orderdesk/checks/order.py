"""Validate and Review, in plain code: the draft order a person reviews, and the checks on their answer.

Prices come from the ERP only. Validate never changes a product or a quantity: it prepares and warns.
"""

from orderdesk.catalogue.ports import ErpPort
from orderdesk.domain.catalogue import Product
from orderdesk.domain.draft import DraftLine, DraftOrder, LineIssue, OrderIssue
from orderdesk.domain.extraction import ExtractedLine, ExtractedOrder
from orderdesk.domain.resolution import LineResolution, ResolvedOrder
from orderdesk.domain.review import ReviewDecision


def draft_line(extracted: ExtractedLine, resolution: LineResolution, evidence: str | None = None) -> DraftLine:
    return _draft_line_from_values(
        line_index=resolution.line_index,
        raw_text=extracted.raw_text,
        quantity=extracted.quantity,
        unit=extracted.unit,
        product=resolution.product,
        evidence=evidence if evidence is not None else resolution.evidence,
    )


def _draft_line_from_values(
    line_index: int,
    raw_text: str,
    quantity: float | None,
    unit: str | None,
    product: Product | None,
    evidence: str | None,
) -> DraftLine:
    issues: list[LineIssue] = []
    if product is None:
        issues.append("no_product")
    if quantity is None or quantity <= 0:
        issues.append("no_quantity")
    if product is not None:
        if product.list_price is None:
            issues.append("no_price")
        if not product.active:
            issues.append("discontinued")
        if unit is not None and unit != product.uom:  # None = the client wrote no unit: nothing to compare
            issues.append("unit_differs")

    unit_price = product.list_price if product is not None else None
    line_total = None
    if quantity is not None and quantity > 0 and unit_price is not None:
        line_total = round(quantity * unit_price, 2)

    return DraftLine(
        line_index=line_index,
        raw_text=raw_text,
        quantity=quantity,
        unit=unit,
        product=product,
        unit_price=unit_price,
        line_total=line_total,
        issues=issues,
        evidence=evidence,
    )


def build_draft(extracted: ExtractedOrder, resolved: ResolvedOrder, erp: ErpPort) -> DraftOrder:
    lines = [draft_line(extracted.lines[resolution.line_index], resolution) for resolution in resolved.lines]

    order_issues: list[OrderIssue] = []
    if resolved.customer is None:
        order_issues.append(OrderIssue(kind="unknown_customer", detail="The sender is not a known customer"))
    elif extracted.po_number:
        existing = erp.find_order_by_client_ref(resolved.customer.id, extracted.po_number)
        if existing is not None:
            order_issues.append(
                OrderIssue(
                    kind="po_already_used",
                    detail=f"PO {extracted.po_number} is already on {existing.order_id}",
                )
            )
    if not resolved.lines:
        order_issues.append(OrderIssue(kind="no_lines", detail="The order has no lines"))

    total = round(sum(line.line_total or 0.0 for line in lines), 2)
    return DraftOrder(
        customer_id=resolved.customer.id if resolved.customer is not None else None,
        po_number=extracted.po_number,
        lines=lines,
        order_issues=order_issues,
        total=total,
    )


def apply_decision(draft: DraftOrder, decision: ReviewDecision, erp: ErpPort) -> tuple[DraftOrder | None, list[str]]:
    """The reviewer's answer, checked by code. Returns (approved draft, []) or (None, every problem found):
    the reviewer sees all problems at once, not one per round trip."""
    if decision.action == "reject":
        if decision.changes:
            return None, ["a reject takes no line changes"]
        return None, []

    errors: list[str] = []
    indexes = {line.line_index for line in draft.lines}
    seen: set[int] = set()
    for change in decision.changes:
        if change.line_index not in indexes:
            errors.append(f"line {change.line_index} does not exist in the draft")
        if change.line_index in seen:
            errors.append(f"line {change.line_index} appears more than once in the changes")
        seen.add(change.line_index)
        if change.code is not None:
            product = erp.get_product(change.code)
            if product is None:
                errors.append(f'line {change.line_index}: unknown product code "{change.code}"')
            elif not product.active:
                errors.append(f'line {change.line_index}: product "{product.code}" is discontinued')
        if change.quantity is not None and change.quantity <= 0:
            errors.append(f"line {change.line_index}: quantity must be greater than 0")
    if errors:
        return None, errors

    changes_by_index = {change.line_index: change for change in decision.changes}
    rebuilt: list[DraftLine] = []
    for old_line in draft.lines:
        change = changes_by_index.get(old_line.line_index)
        if change is not None and change.remove:
            continue

        product = old_line.product
        quantity = old_line.quantity
        evidence = old_line.evidence
        if change is not None:
            if change.code is not None:
                product = erp.get_product(change.code)
                evidence = f"chosen by {decision.reviewer}"
            if change.quantity is not None:
                quantity = change.quantity

        rebuilt.append(
            _draft_line_from_values(
                line_index=old_line.line_index,
                raw_text=old_line.raw_text,
                quantity=quantity,
                unit=old_line.unit,
                product=product,
                evidence=evidence,
            )
        )

    if not rebuilt:
        return None, ["the draft has no lines left"]

    for line in rebuilt:
        if line.product is None:
            errors.append(f"line {line.line_index} has no product: choose one or remove it")
        elif not line.product.active:  # the ERP cannot sell it: the write would fail later
            errors.append(f'line {line.line_index}: product "{line.product.code}" is discontinued: choose another')
        if line.quantity is None or line.quantity <= 0:
            errors.append(f"line {line.line_index} has no quantity: set a quantity")
    errors.extend(issue.detail for issue in draft.blocking)
    if errors:
        return None, errors

    total = round(sum(line.line_total or 0.0 for line in rebuilt), 2)
    approved = DraftOrder(
        customer_id=draft.customer_id,
        po_number=draft.po_number,
        lines=rebuilt,
        order_issues=draft.order_issues,
        total=total,
    )
    return approved, []
