from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from orderdesk.catalogue.memory import MemoryCatalogue
from orderdesk.checks.order import apply_decision
from orderdesk.domain.catalogue import Customer, Product
from orderdesk.domain.classification import EmailClassification
from orderdesk.domain.draft import DraftLine, DraftOrder, OrderIssue
from orderdesk.domain.extraction import ExtractedLine, ExtractedOrder, ProductInfo
from orderdesk.domain.review import LineChange, ReviewDecision
from orderdesk.workflow.graph import build_graph
from tests.factories import make_email
from tests.fakes import FakeLLM, FakeStorage

PROFILE = "ACME sells taps and shower systems."
CUSTOMER = Customer(id=2, name="Bains & Co")
KNOWN_EMAIL = "showroom@bains-and-co.example"
PRODUCT = Product(code="PF60375", name="Mitigeur lavabo", family="Mitigeur lavabo", list_price=175.0)
CATALOGUE = MemoryCatalogue([PRODUCT], {KNOWN_EMAIL: CUSTOMER})


def classified_as(label: str) -> FakeLLM:
    return FakeLLM(EmailClassification(evidence="test evidence", label=label, confidence="high"))


class NoAgent:
    def bind_tools(self, tools):
        raise AssertionError("the agent must not run in this test")


def make_review_graph(extracted: ExtractedOrder, catalogue: MemoryCatalogue = CATALOGUE):
    graph = build_graph(
        classified_as("new_order"),
        FakeLLM(extracted),
        FakeLLM(None),
        NoAgent(),
        PROFILE,
        FakeStorage(),
        catalogue,
        catalogue,
        catalogue,
        checkpointer=InMemorySaver(),
    )
    return graph


def test_new_order_pauses_and_approve_keeps_draft() -> None:
    line = ExtractedLine(
        raw_text="PF60375 x 2",
        quantity=2,
        unit="piece",
        product=ProductInfo(kind="named", code_as_written="PF60375"),
    )
    graph = make_review_graph(ExtractedOrder(po_number="4471", lines=[line]))
    config = {"configurable": {"thread_id": "review-approve"}}

    paused = graph.invoke({"email": make_email(sender=KNOWN_EMAIL)}, config)

    assert "__interrupt__" in paused
    assert paused["draft_order"] is not None
    assert paused.get("approved_order") is None

    resumed = graph.invoke(
        Command(resume={"action": "approve", "reviewer": "manager-1"}),
        config,
    )

    assert resumed["approved_order"] == resumed["draft_order"]
    assert resumed["review_decision"].reviewer == "manager-1"


def test_reject_sets_no_approved_order() -> None:
    line = ExtractedLine(
        raw_text="PF60375 x 1",
        quantity=1,
        product=ProductInfo(kind="named", code_as_written="PF60375"),
    )
    graph = make_review_graph(ExtractedOrder(lines=[line]))
    config = {"configurable": {"thread_id": "review-reject"}}

    graph.invoke({"email": make_email(sender=KNOWN_EMAIL)}, config)
    resumed = graph.invoke(Command(resume={"action": "reject", "reviewer": "manager-1"}), config)

    assert resumed["approved_order"] is None


def test_change_picks_product_for_unresolved_line() -> None:
    line = ExtractedLine(
        raw_text="2 mystery taps",
        quantity=2,
        unit="piece",
        product=ProductInfo(kind="missing", text=None),
    )
    graph = make_review_graph(ExtractedOrder(lines=[line]))
    config = {"configurable": {"thread_id": "review-change"}}

    paused = graph.invoke({"email": make_email(sender=KNOWN_EMAIL)}, config)
    interrupt = paused["__interrupt__"][0]
    assert "draft" in interrupt.value

    resumed = graph.invoke(
        Command(
            resume={
                "action": "approve",
                "reviewer": "manager-2",
                "changes": [{"line_index": 0, "code": "PF60375"}],
            }
        ),
        config,
    )

    approved = resumed["approved_order"]
    assert approved.lines[0].product.code == "PF60375"
    assert approved.lines[0].line_total == 350.0
    assert approved.lines[0].evidence == "chosen by manager-2"


def test_remove_unresolved_line_after_graph_pauses_again() -> None:
    kept = ExtractedLine(
        raw_text="PF60375 x 1",
        quantity=1,
        product=ProductInfo(kind="named", code_as_written="PF60375"),
    )
    line = ExtractedLine(
        raw_text="mystery tap",
        quantity=1,
        product=ProductInfo(kind="missing", text=None),
    )
    graph = make_review_graph(ExtractedOrder(lines=[kept, line]))
    config = {"configurable": {"thread_id": "review-remove"}}

    graph.invoke({"email": make_email(sender=KNOWN_EMAIL)}, config)
    paused_again = graph.invoke(
        Command(resume={"action": "approve", "reviewer": "manager-1", "changes": []}),
        config,
    )
    assert "__interrupt__" in paused_again

    resumed = graph.invoke(
        Command(
            resume={
                "action": "approve",
                "reviewer": "manager-1",
                "changes": [{"line_index": 1, "remove": True}],
            }
        ),
        config,
    )
    assert resumed["approved_order"] is not None


def test_malformed_answer_pauses_again() -> None:
    line = ExtractedLine(
        raw_text="PF60375 x 1",
        quantity=1,
        product=ProductInfo(kind="named", code_as_written="PF60375"),
    )
    graph = make_review_graph(ExtractedOrder(lines=[line]))
    config = {"configurable": {"thread_id": "review-malformed"}}

    graph.invoke({"email": make_email(sender=KNOWN_EMAIL)}, config)
    paused_again = graph.invoke(Command(resume={"action": "maybe"}), config)

    assert "__interrupt__" in paused_again


def test_other_label_never_reaches_review() -> None:
    graph = build_graph(
        classified_as("other"),
        FakeLLM(ExtractedOrder()),
        FakeLLM(None),
        NoAgent(),
        PROFILE,
        FakeStorage(),
        CATALOGUE,
        CATALOGUE,
        CATALOGUE,
        checkpointer=InMemorySaver(),
    )
    result = graph.invoke(
        {"email": make_email(sender=KNOWN_EMAIL)},
        {"configurable": {"thread_id": "review-other"}},
    )
    assert "__interrupt__" not in result


def _draft_for_direct_tests(lines: list[DraftLine], order_issues: list[OrderIssue] | None = None) -> DraftOrder:
    return DraftOrder(customer_id=2, po_number="4471", lines=lines, order_issues=order_issues or [], total=175)


def test_unknown_code_discontinued_quantity_and_duplicate_changes_are_refused() -> None:
    line = DraftLine(
        line_index=0,
        raw_text="tap",
        quantity=1,
        unit="piece",
        product=PRODUCT,
        unit_price=175,
        line_total=175,
    )
    draft = _draft_for_direct_tests([line])

    approved, errors = apply_decision(
        draft,
        ReviewDecision(action="approve", reviewer="manager", changes=[LineChange(line_index=0, code="XX")]),
        CATALOGUE,
    )
    assert approved is None
    assert errors and "unknown product code" in errors[0]

    discontinued = Product(code="OLD", name="Old", family="Old", active=False)
    discontinued_catalogue = MemoryCatalogue([discontinued])
    approved, errors = apply_decision(
        draft,
        ReviewDecision(action="approve", reviewer="manager", changes=[LineChange(line_index=0, code="OLD")]),
        discontinued_catalogue,
    )
    assert approved is None
    assert errors and "discontinued" in errors[0]

    approved, errors = apply_decision(
        draft,
        ReviewDecision(action="approve", reviewer="manager", changes=[LineChange(line_index=0, quantity=0)]),
        CATALOGUE,
    )
    assert approved is None
    assert errors and "greater than 0" in errors[0]

    approved, errors = apply_decision(
        draft,
        ReviewDecision(
            action="approve",
            reviewer="manager",
            changes=[
                LineChange(line_index=0, quantity=2),
                LineChange(line_index=0, remove=True),
            ],
        ),
        CATALOGUE,
    )
    assert approved is None
    assert errors and "more than once" in errors[0]


def test_blocked_draft_cannot_be_approved_but_can_be_rejected() -> None:
    line = DraftLine(
        line_index=0,
        raw_text="tap",
        quantity=1,
        unit="piece",
        product=PRODUCT,
        unit_price=175,
        line_total=175,
    )
    draft = _draft_for_direct_tests(
        [line],
        [OrderIssue(kind="po_already_used", detail="PO 4471 is already on SO-2291")],
    )

    approved, errors = apply_decision(draft, ReviewDecision(action="approve", reviewer="manager"), CATALOGUE)
    assert approved is None
    assert "SO-2291" in errors[0]

    approved, errors = apply_decision(draft, ReviewDecision(action="reject", reviewer="manager"), CATALOGUE)
    assert approved is None
    assert errors == []


def test_every_problem_is_reported_at_once() -> None:
    no_product = DraftLine(line_index=0, raw_text="mystery tap", quantity=1, unit=None, product=None,
                           unit_price=None, line_total=None, issues=["no_product"])
    draft = _draft_for_direct_tests(
        [no_product], [OrderIssue(kind="po_already_used", detail="PO 4471 is already on SO-2291")]
    )

    approved, errors = apply_decision(draft, ReviewDecision(action="approve", reviewer="manager"), CATALOGUE)

    assert approved is None
    assert errors == ["line 0 has no product: choose one or remove it", "PO 4471 is already on SO-2291"]


def test_a_discontinued_product_already_on_a_line_cannot_be_approved() -> None:
    old = Product(code="OLD", name="Old", family="Old", list_price=10, active=False)
    line = DraftLine(line_index=0, raw_text="old tap", quantity=1, unit=None, product=old, unit_price=10,
                     line_total=10, issues=["discontinued"])

    approved, errors = apply_decision(_draft_for_direct_tests([line]),
                                      ReviewDecision(action="approve", reviewer="manager"), CATALOGUE)

    assert approved is None
    assert "discontinued" in errors[0]


def test_the_second_pause_tells_the_reviewer_what_is_wrong() -> None:
    line = ExtractedLine(raw_text="mystery tap", quantity=1, product=ProductInfo(kind="missing"))
    graph = make_review_graph(ExtractedOrder(lines=[line]))
    config = {"configurable": {"thread_id": "review-errors"}}
    graph.invoke({"email": make_email(sender=KNOWN_EMAIL)}, config)

    missing = graph.invoke(Command(resume={"action": "approve", "reviewer": "manager-1"}), config)
    malformed = graph.invoke(Command(resume={"action": "maybe"}), config)

    assert missing["__interrupt__"][0].value["errors"] == ["line 0 has no product: choose one or remove it"]
    assert malformed["__interrupt__"][0].value["errors"]  # the validation message, not a crash
    assert malformed.get("approved_order") is None
