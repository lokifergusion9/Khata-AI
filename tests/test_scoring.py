import json
from pathlib import Path

import pytest

from evals.scoring import FIELDS, aggregate, field_match, score_case

EVALS = Path(__file__).resolve().parent.parent / "evals"


def load(name):
    return [json.loads(line) for line in (EVALS / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def test_normalisation():
    assert field_match("shop_name", {"shop_name": "sharma  traders,"}, {"shop_name": "Sharma Traders"})
    assert field_match("transaction_id", {"transaction_id": "UTR: 4122 3344"}, {"transaction_id": "41223344"})
    assert field_match("amount", {"amount": "15000"}, {"amount": 15000})
    assert field_match("client_name", {"client_name": "Sharma"}, {"client_name": ["Sharma ji", "Sharma"]})
    assert field_match("location", {"location": ""}, {})
    assert not field_match("status", {"status": "Pending"}, {"status": "Received"})


def test_multi_transaction_pairs_by_amount():
    gold = [
        {"shop_name": "A", "amount": 100, "status": "Received"},
        {"shop_name": "B", "amount": 200, "status": "Pending"},
    ]
    predicted = [
        {"shop_name": "B", "amount": "200", "status": "Pending"},
        {"shop_name": "A", "amount": "100", "status": "Received"},
    ]
    result = score_case(predicted, gold)
    assert (result["tp"], result["fp"], result["fn"]) == (2, 0, 0)
    assert result["perfect"]


def test_extra_and_missing_transactions():
    assert score_case([{"amount": "5"}], [])["fp"] == 1
    assert score_case([], [{"amount": 5}])["fn"] == 1
    assert score_case([], [])["perfect"]


def test_hallucinated_field_is_counted():
    result = score_case(
        [{"shop_name": "A", "gst_number": "08ABC", "amount": "1", "status": "Received"}],
        [{"shop_name": "A", "amount": 1, "status": "Received"}],
    )
    assert result["hallucinated"] == 1
    assert not result["perfect"]


def test_aggregate():
    results = [
        score_case([{"shop_name": "A", "amount": "1", "status": "Received"}], [{"shop_name": "A", "amount": 1, "status": "Received"}]),
        score_case([{"amount": "7"}], []),
    ]
    m = aggregate(results)
    assert (m["precision"], m["recall"]) == (0.5, 1.0)
    assert m["perfect_rate"] == 0.5


def test_heldout_set_shares_nothing_with_the_dev_set():
    dev, heldout = load("dataset.jsonl"), load("heldout.jsonl")
    assert not {c["id"] for c in dev} & {c["id"] for c in heldout}
    assert not {c["message"] for c in dev} & {c["message"] for c in heldout}


@pytest.mark.parametrize("name", ["dataset.jsonl", "heldout.jsonl"])
def test_dataset_is_well_formed(name):
    cases = load(name)
    assert len({c["id"] for c in cases}) == len(cases)
    assert len({c["message"] for c in cases}) == len(cases)  # rows map back to cases by message text
    for case in cases:
        for gold in case["expected"]:
            assert set(gold) <= set(FIELDS), case["id"]
            assert "amount" in gold and "status" in gold, case["id"]
