import re

# same order as extractor.to_row; the last two columns are copied by Python, not scored
ROW_KEYS = [
    "shop_name", "client_name", "location", "transaction_id", "amount",
    "bank_account", "gst_number", "status", "timestamp", "content",
]
FIELDS = ROW_KEYS[:8]
TEXT_FIELDS = ["shop_name", "client_name", "location", "transaction_id", "bank_account", "gst_number"]


def norm_text(value):
    # "Sharma Traders," == "sharma  traders"
    return " ".join(re.sub(r"[^\w]", " ", str(value).lower()).split())


def norm_alnum(value):
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


def norm_id(value):
    # "UTR: 4122-3344" == "41223344", the label in front is not part of the id
    return re.sub(r"^(UTR|UPI|IMPS|NEFT|RTGS|REF|TXN|CHEQUE|CHQ)(NO)?", "", norm_alnum(value))


def norm_amount(value):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


NORMALIZERS = {
    "shop_name": norm_text,
    "client_name": norm_text,
    "location": norm_text,
    "transaction_id": norm_id,
    "amount": norm_amount,
    "bank_account": norm_alnum,
    "gst_number": norm_alnum,
    "status": str,
}


def alternatives(gold, field):
    # a gold value can be one string or a list of accepted answers; missing means ""
    value = gold.get(field, 0 if field == "amount" else "Unknown" if field == "status" else "")
    return value if isinstance(value, list) else [value]


def field_match(field, predicted, gold):
    norm = NORMALIZERS[field]
    return norm(predicted.get(field, "")) in {norm(v) for v in alternatives(gold, field)}


def row_to_dict(row):
    return dict(zip(ROW_KEYS, row))


def pair_score(predicted, gold):
    # amount counts double: it is what tells two transactions in one message apart
    return sum((2 if f == "amount" else 1) for f in FIELDS if field_match(f, predicted, gold))


def match_transactions(predicted, gold):
    # greedy pairing by score, every pair allowed; unpaired = false positive / false negative
    candidates = sorted(
        ((pair_score(p, g), i, j) for i, p in enumerate(predicted) for j, g in enumerate(gold)),
        reverse=True,
    )
    used_p, used_g, pairs = set(), set(), []
    for _, i, j in candidates:
        if i not in used_p and j not in used_g:
            used_p.add(i)
            used_g.add(j)
            pairs.append((predicted[i], gold[j]))
    false_pos = [p for i, p in enumerate(predicted) if i not in used_p]
    false_neg = [g for j, g in enumerate(gold) if j not in used_g]
    return pairs, false_pos, false_neg


def score_case(predicted, gold):
    pairs, false_pos, false_neg = match_transactions(predicted, gold)
    fields = {f: [field_match(f, p, g) for p, g in pairs] for f in FIELDS}
    hallucinated = missed = 0
    for p, g in pairs:
        for f in TEXT_FIELDS:
            gold_empty = all(NORMALIZERS[f](v) == "" for v in alternatives(g, f))
            pred_empty = NORMALIZERS[f](p.get(f, "")) == ""
            hallucinated += gold_empty and not pred_empty
            missed += not gold_empty and pred_empty and not field_match(f, p, g)
    mismatches = [
        {"field": f, "predicted": p.get(f, ""), "expected": alternatives(g, f)}
        for p, g in pairs for f in FIELDS if not field_match(f, p, g)
    ]
    return {
        "tp": len(pairs),
        "fp": len(false_pos),
        "fn": len(false_neg),
        "fields": fields,
        "hallucinated": hallucinated,
        "missed": missed,
        "perfect": not false_pos and not false_neg and not mismatches,
        "mismatches": mismatches,
    }


def safe_div(a, b):
    return a / b if b else 0.0


def aggregate(results):
    tp = sum(r["tp"] for r in results)
    fp = sum(r["fp"] for r in results)
    fn = sum(r["fn"] for r in results)
    precision, recall = safe_div(tp, tp + fp), safe_div(tp, tp + fn)
    field_acc = {
        f: safe_div(sum(sum(r["fields"][f]) for r in results), sum(len(r["fields"][f]) for r in results))
        for f in FIELDS
    }
    text_slots = tp * len(TEXT_FIELDS)
    return {
        "cases": len(results),
        "transactions": {"tp": tp, "fp": fp, "fn": fn},
        "precision": precision,
        "recall": recall,
        "f1": safe_div(2 * precision * recall, precision + recall),
        "field_accuracy": field_acc,
        "field_accuracy_mean": safe_div(sum(field_acc.values()), len(field_acc)),
        "hallucination_rate": safe_div(sum(r["hallucinated"] for r in results), text_slots),
        "missed_field_rate": safe_div(sum(r["missed"] for r in results), text_slots),
        "perfect_rate": safe_div(sum(r["perfect"] for r in results), len(results)),
    }
