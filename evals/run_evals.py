"""Score the transaction extractor against the labelled dataset.

    uv run python -m evals.run_evals                     # full dataset, model from .env
    uv run python -m evals.run_evals --batch-size 1      # one message per LLM call
    uv run python -m evals.run_evals --model llama-3.3-70b-versatile --limit 20
    uv run python -m evals.run_evals --prompt evals/prompts/v1.txt --repeats 3   # an older prompt, 3 runs

Messages are sent in shuffled batches, like a real run sends one group's new messages
in one call. A report (JSON + Markdown) is written to evals/reports/.
"""
import argparse
import asyncio
import json
import os
import random
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
# core.config needs these at import time, the extractor itself does not use them
for name in ("GOOGLE_APPLICATION_CREDENTIALS", "WHATSAPP_SERVER_DIR", "SPREADSHEET_ID"):
    os.environ.setdefault(name, "unused-in-evals")

from langchain_groq import ChatGroq  # noqa: E402

from core import extractor  # noqa: E402
from core.config import EXTRACT_BATCH_SIZE, GROQ_MODEL  # noqa: E402
from core.whatsapp import parse_message  # noqa: E402
from evals.scoring import FIELDS, aggregate, row_to_dict, score_case  # noqa: E402

DATASET = ROOT / "evals" / "dataset.jsonl"
REPORTS = ROOT / "evals" / "reports"
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_WAIT = 65  # seconds; Groq limits are per minute


class CountingLLM:
    # wraps the chat model to count calls (retries included) and tokens
    def __init__(self, llm):
        self.llm, self.calls, self.input_tokens, self.output_tokens = llm, 0, 0, 0

    async def ainvoke(self, text):
        self.calls += 1
        response = await self.llm.ainvoke(text)
        usage = getattr(response, "usage_metadata", None) or {}
        self.input_tokens += usage.get("input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)
        return response


def load_cases(path=DATASET, limit=None):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    cases = [json.loads(line) for line in lines if line.strip()]
    return cases[:limit] if limit else cases


def to_raw(case, index):
    # same shape as whatsapp-mcp list_messages output, so parse_message is exercised too
    return f"[2026-10-25 {9 + index // 60:02d}:{index % 60:02d}:00] Chat: Eval Payments From: Tester: {case['message']}"


async def run_batch(batch, llm):
    raws = [to_raw(case, i) for i, case in batch]
    seconds = 0.0
    for attempt in range(1, RATE_LIMIT_RETRIES + 2):
        start = time.perf_counter()
        try:
            # one LLM call per batch: without batch_size, extract() would re-chunk by EXTRACT_BATCH_SIZE
            rows = await extractor.extract(raws, llm=llm, batch_size=len(raws))
            error = None
        except Exception as e:  # the whole batch failed, every case in it scores as missed
            rows, error = [], str(e)[:300]
        seconds += time.perf_counter() - start
        # a rate limit is the provider's quota, not a model mistake: wait for the next minute and retry
        if error and "429" in error and attempt <= RATE_LIMIT_RETRIES:
            print(f"  rate limited, waiting {RATE_LIMIT_WAIT}s (retry {attempt}/{RATE_LIMIT_RETRIES})")
            await asyncio.sleep(RATE_LIMIT_WAIT)
            continue
        break

    # rows carry the original message text (column 10), which maps them back to their case
    by_content = defaultdict(list)
    for row in rows:
        by_content[row[9]].append(row_to_dict(row))
    out = []
    for (i, case), raw in zip(batch, raws):
        predicted = by_content.get(parse_message(raw)[1], [])
        out.append({"case": case, "predicted": predicted, "error": error, **score_case(predicted, case["expected"])})
    return out, seconds


def pct(x):
    return f"{100 * x:.1f}%"


def markdown_report(meta, overall, per_run, per_category, results):
    lines = [
        f"# Extraction eval: `{meta['model']}`, prompt `{meta['prompt']}`, dataset `{meta['dataset']}`",
        "",
        f"{meta['timestamp']} · {overall['cases'] // meta['repeats']} messages x {meta['repeats']} runs · "
        f"batch size {meta['batch_size']} · "
        f"{meta['llm_calls']} LLM calls · {meta['seconds']:.1f}s · "
        f"{meta['input_tokens']} in / {meta['output_tokens']} out tokens",
        "",
        "## Overall",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Transaction precision | {pct(overall['precision'])} |",
        f"| Transaction recall | {pct(overall['recall'])} |",
        f"| Transaction F1 | {pct(overall['f1'])} |",
        f"| Mean field accuracy (matched transactions) | {pct(overall['field_accuracy_mean'])} |",
        f"| Hallucinated fields | {pct(overall['hallucination_rate'])} |",
        f"| Missed fields | {pct(overall['missed_field_rate'])} |",
        f"| Messages fully correct | {pct(overall['perfect_rate'])} |",
        "",
        "## Per run",
        "",
        "| Run | Precision | Recall | Field acc. | Fully correct |",
        "|---|---|---|---|---|",
        *(
            f"| {n} | {pct(m['precision'])} | {pct(m['recall'])} | {pct(m['field_accuracy_mean'])} | {pct(m['perfect_rate'])} |"
            for n, m in enumerate(per_run, 1)
        ),
        "",
        "## Field accuracy",
        "",
        "| " + " | ".join(FIELDS) + " |",
        "|" + "---|" * len(FIELDS),
        "| " + " | ".join(pct(overall["field_accuracy"][f]) for f in FIELDS) + " |",
        "",
        "## By category",
        "",
        "| Category | Messages | Precision | Recall | Field acc. | Fully correct |",
        "|---|---|---|---|---|---|",
    ]
    for category, m in per_category.items():
        t = m["transactions"]
        # a category with no gold and no predicted transactions has nothing to measure but "fully correct"
        precision = pct(m["precision"]) if t["tp"] + t["fp"] else "–"
        recall = pct(m["recall"]) if t["tp"] + t["fn"] else "–"
        field_acc = pct(m["field_accuracy_mean"]) if t["tp"] else "–"
        lines.append(
            f"| {category} | {m['cases']} | {precision} | {recall} | {field_acc} | {pct(m['perfect_rate'])} |"
        )
    failures = [r for r in results if not r["perfect"]]
    lines += ["", f"## Failures ({len(failures)})", ""]
    for r in failures:
        case = r["case"]
        lines.append(f"- **{case['id']}** run {r['run']} ({case['category']}): `{case['message']}`")
        if r["error"]:
            lines.append(f"  - batch error: {r['error']}")
        if r["fp"]:
            lines.append(f"  - {r['fp']} extra transaction(s)")
        if r["fn"]:
            lines.append(f"  - {r['fn']} missed transaction(s)")
        for m in r["mismatches"]:
            lines.append(f"  - `{m['field']}`: got `{m['predicted']}`, expected `{m['expected']}`")
    return "\n".join(lines) + "\n"


async def main(args):
    cases = load_cases(args.dataset, args.limit)
    dataset_name = Path(args.dataset).stem
    order = list(enumerate(cases))
    random.Random(args.seed).shuffle(order)
    batches = [order[i:i + args.batch_size] for i in range(0, len(order), args.batch_size)]

    prompt_name = "current"
    if args.prompt:
        # compare prompt versions without editing the code: the file replaces extractor.PROMPT
        extractor.PROMPT = Path(args.prompt).read_text(encoding="utf-8")
        prompt_name = Path(args.prompt).stem

    model = args.model or GROQ_MODEL
    llm = CountingLLM(ChatGroq(model=model, temperature=0, max_retries=6))
    print(f"model {model}, prompt {prompt_name}: {len(cases)} messages in {len(batches)} batches x {args.repeats} runs")

    # outputs vary between runs even at temperature 0, so one run is not enough to compare prompts
    results, per_run, seconds = [], [], 0.0
    for run in range(1, args.repeats + 1):
        run_results = []
        for n, batch in enumerate(batches, 1):
            out, s = await run_batch(batch, llm)
            run_results += [{**r, "run": run} for r in out]
            seconds += s
            print(f"run {run} batch {n}/{len(batches)}: {s:.1f}s, {sum(r['perfect'] for r in out)}/{len(out)} fully correct")
        per_run.append(aggregate(run_results))
        results += run_results

    results.sort(key=lambda r: (r["case"]["id"], r["run"]))
    overall = aggregate(results)
    by_cat = defaultdict(list)
    for r in results:
        by_cat[r["case"]["category"]].append(r)
    per_category = {c: aggregate(rs) for c, rs in sorted(by_cat.items())}

    meta = {
        "model": model,
        "prompt": prompt_name,
        "dataset": dataset_name,
        "repeats": args.repeats,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "batch_size": args.batch_size,
        "seed": args.seed,
        "llm_calls": llm.calls,
        "input_tokens": llm.input_tokens,
        "output_tokens": llm.output_tokens,
        "seconds": seconds,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w.-]", "-", model)
    stem = f"{datetime.now():%Y%m%d-%H%M%S}_{dataset_name}_{slug}_{prompt_name}_b{args.batch_size}_x{args.repeats}"
    report = {"meta": meta, "overall": overall, "per_run": per_run, "per_category": per_category, "results": results}
    (REPORTS / f"{stem}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    markdown = markdown_report(meta, overall, per_run, per_category, results)
    (REPORTS / f"{stem}.md").write_text(markdown, encoding="utf-8")

    print()
    print(markdown.split("## Failures")[0])
    print(f"report: evals/reports/{stem}.md")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", help="Groq model id (default: GROQ_MODEL from .env)")
    parser.add_argument(
        "--batch-size", type=int, default=EXTRACT_BATCH_SIZE,
        help=f"messages per LLM call (default: EXTRACT_BATCH_SIZE from .env, now {EXTRACT_BATCH_SIZE})",
    )
    parser.add_argument("--limit", type=int, help="only the first N cases")
    parser.add_argument("--seed", type=int, default=7, help="shuffle seed for the batches")
    parser.add_argument("--repeats", type=int, default=1, help="run the whole dataset N times (default 1)")
    parser.add_argument(
        "--dataset", default=DATASET,
        help="cases file (default: evals/dataset.jsonl, the dev set; evals/heldout.jsonl is never used for tuning)",
    )
    parser.add_argument("--prompt", help="prompt file to use instead of the current one, e.g. evals/prompts/v1.txt")
    asyncio.run(main(parser.parse_args()))
