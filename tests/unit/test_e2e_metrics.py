"""The end-to-end eval metrics and its case file: no LLM, no LangSmith."""

import pytest

from evals.e2e_metrics import score_email, summarize
from evals.run_e2e import load_cases
from orderdesk.domain.classification import EmailClassification
from orderdesk.domain.email import EmailContext
from orderdesk.profiles.demo_catalogue import make_demo_catalogue

EXPECTED = {"label": "new_order", "po_number": "4471", "lines": [{"code": "CAR-35"}, {"code": None}]}


def outputs(*codes, label="new_order", po="BC 4471") -> dict:
    return {"label": label, "po_number": po, "lines": [{"code": code} for code in codes], "seconds": 1.0}


def test_the_right_products_and_an_honest_handover() -> None:
    scores = score_email(outputs("CAR-35", None), EXPECTED)

    assert scores["email_correct"] and scores["po_correct"]
    assert (scores["lines_correct"], scores["silent_wrong"], scores["lines_automatic"]) == (2, 0, 1)


def test_a_product_where_a_person_should_decide_is_silent_wrong() -> None:
    scores = score_email(outputs("CAR-35", "CAR-40"), EXPECTED)

    assert scores["silent_wrong"] == 1 and not scores["email_correct"]


def test_a_handover_where_a_product_was_expected_is_missed_not_wrong() -> None:
    scores = score_email(outputs(None, None), EXPECTED)

    assert (scores["missed"], scores["silent_wrong"]) == (1, 0)


def test_a_line_the_client_did_not_write_is_wrong_and_a_lost_line_is_missed() -> None:
    assert score_email(outputs("CAR-35", None, "PF60378"), EXPECTED)["silent_wrong"] == 1
    assert score_email(outputs("CAR-35"), EXPECTED)["missed"] == 1


def test_the_verdict() -> None:
    good = score_email(outputs("CAR-35", None), EXPECTED)
    wrong = score_email(outputs("CAR-35", "CAR-40"), EXPECTED)
    failed = {"providers_failed": True, "seconds": 1.0}

    assert summarize([good] * 3)["verdict"] == "PASS"
    assert summarize([good] * 3 + [wrong])["verdict"] == "FAIL"
    assert summarize([good, score_email(outputs(label="other"), EXPECTED)])["verdict"] == "FAIL"
    assert summarize([good, failed])["verdict"] == "RERUN"


# ---------- the case file ----------

CATALOGUE = make_demo_catalogue()


@pytest.mark.parametrize("case", load_cases(), ids=lambda case: case["id"])
def test_every_case_is_a_valid_email_with_real_products(case) -> None:
    EmailContext(tenant_id=999, email_id=1, **case["email"])
    expected = case["expected"]
    assert expected["label"] in EmailClassification.model_fields["label"].annotation.__args__
    for line in expected["lines"]:
        assert line["code"] is None or CATALOGUE.get_product(line["code"]) is not None, line
    if expected["label"] != "new_order":
        assert expected["lines"] == []


def test_case_ids_are_unique() -> None:
    ids = [case["id"] for case in load_cases()]
    assert len(ids) == len(set(ids))
