import argparse
import asyncio
import json
import re
import sys
from collections import defaultdict
from datetime import date
from core.config import SALES_KEYWORDS, SUMMARY_CHAT_JID, load_tools, tool_text
from core.sheets import read_ledger
from core.whatsapp import send_message

# ledger columns (0-based): A Shop, B Client, ..., E Amount, ..., H Status, I Date & Time
SHOP, CLIENT, AMOUNT, STATUS, DATE_TIME = 0, 1, 4, 7, 8


def to_amount(value) -> float:
    # "₹15,000" / "15,000" / " 15000 " -> 15000.0, anything unparseable -> 0
    cleaned = re.sub(r"[₹,\s]|Rs\.?", "", str(value))
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def format_inr(amount: float) -> str:
    # Indian digit grouping: 145000 -> "₹1,45,000"
    whole, paise = divmod(round(amount * 100), 100)
    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        head = ",".join(re.findall(r"\d{1,2}", head[::-1]))[::-1]
        digits = f"{head},{tail}"
    return f"₹{digits}" + (f".{paise:02d}" if paise else "")


def rows_for_day(rows, day: date):
    # column I is filled by Python as "YYYY-MM-DD HH:MM:SS", so a prefix match is exact
    return [row for row in rows if row[DATE_TIME].startswith(day.isoformat())]


def compute_stats(rows) -> dict:
    stats = {
        "count": len(rows), "total": 0.0,
        "received_count": 0, "received": 0.0,
        "pending_count": 0, "pending": 0.0,
        "unknown_count": 0, "unknown": 0.0,
        "top_client": None, "top_client_amount": 0.0,
    }
    by_client = defaultdict(float)

    for row in rows:
        amount = to_amount(row[AMOUNT])
        stats["total"] += amount
        status = row[STATUS].lower() if row[STATUS].lower() in ("received", "pending") else "unknown"
        stats[f"{status}_count"] += 1
        stats[status] += amount
        # Client Name is often empty, so fall back to the shop name
        by_client[row[CLIENT] or row[SHOP] or "Unknown"] += amount

    if by_client:
        stats["top_client"], stats["top_client_amount"] = max(by_client.items(), key=lambda item: item[1])
    return stats


def format_summary(stats: dict, day: date) -> str:
    header = f"📊 Daily Summary — {day:%d %b %Y}"
    if stats["count"] == 0:
        return f"{header}\nNo transactions today"

    lines = [
        header,
        f"Transactions: {stats['count']}",
        f"Total: {format_inr(stats['total'])}",
        f"✅ Received: {stats['received_count']} ({format_inr(stats['received'])})",
        f"⏳ Pending: {stats['pending_count']} ({format_inr(stats['pending'])})",
    ]
    if stats["unknown_count"]:
        lines.append(f"❔ Unknown: {stats['unknown_count']} ({format_inr(stats['unknown'])})")
    lines.append(f"Top client: {stats['top_client']} ({format_inr(stats['top_client_amount'])})")
    return "\n".join(lines)


async def check_target(tools, chat_jid):
    # never post into a monitored group: the next client.py run would read the summary as transactions
    if not chat_jid:
        raise SystemExit("SUMMARY_CHAT_JID is empty in .env")
    if chat_jid.endswith("@g.us"):
        # keep include_last_message at its default: with False the server's SQL breaks and returns nothing
        text = tool_text(await tools["get_chat"].ainvoke({"chat_jid": chat_jid}))
        if not text:
            raise SystemExit(f"refusing to send: group {chat_jid} not found")
        chat = json.loads(text)
        name = (chat.get("name") or "").lower()
        if any(keyword in name for keyword in SALES_KEYWORDS):
            raise SystemExit(f"refusing to send: '{chat.get('name')}' is a monitored group (name has a keyword)")


async def main(day: date, dry_run: bool):
    tools = await load_tools(["google-sheets"] if dry_run else ["google-sheets", "whatsapp"])

    rows = await read_ledger(tools)
    text = format_summary(compute_stats(rows_for_day(rows, day)), day)

    if dry_run:
        print("DRY RUN, not sent:\n")
        print(text)
        return

    await check_target(tools, SUMMARY_CHAT_JID)
    result = await send_message(tools, SUMMARY_CHAT_JID, text)
    if not result.get("success"):
        raise SystemExit(f"send failed: {result.get('message')}")
    print(f"sent to {SUMMARY_CHAT_JID}:\n")
    print(text)


if __name__ == "__main__":
    # emojis and ₹ must print even when output is redirected (e.g. from Task Scheduler)
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Send the daily ledger summary to WhatsApp")
    parser.add_argument("--dry-run", action="store_true", help="print the message instead of sending it")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today(), help="day to summarise, YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    asyncio.run(main(args.date, args.dry_run))
