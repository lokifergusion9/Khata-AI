import json
import re
from typing import Literal
from langchain_groq import ChatGroq
from pydantic import BaseModel, TypeAdapter, field_validator
from .config import EXTRACT_BATCH_SIZE, GROQ_MODEL
from .whatsapp import parse_message


class Transaction(BaseModel):
    shop_name: str = ""
    client_name: str = ""
    location: str = ""
    transaction_id: str = ""
    amount: float
    bank_account: str = ""
    gst_number: str = ""
    status: Literal["Received", "Pending", "Unknown"] = "Unknown"
    source: int

    @field_validator("amount", mode="before")
    @classmethod
    def clean_amount(cls, value):
        # the LLM sometimes returns "₹15,000" instead of 15000
        if isinstance(value, str):
            value = re.sub(r"[₹,\s]|Rs\.?", "", value)
        return value


TRANSACTIONS = TypeAdapter(list[Transaction])

PROMPT = """Extract financial transactions from these numbered WhatsApp messages.

Return ONLY a JSON array. No markdown, no explanation.
Each item has these keys:
- shop_name: the business or firm name (e.g. "Lakshmi General Store", "Rao Enterprises"), without the city
- client_name: a person's name only (e.g. "Mr. Vikram Rao", "Gupta ji"). Never put a business name here
- location: the city or town
- transaction_id, bank_account, gst_number: strings
- shop_name, client_name, location, transaction_id, bank_account, gst_number are "" if not mentioned
- amount: a plain number, no currency sign or commas (e.g. 15000)
- status: "Received" if the message says the money was paid or arrived, "Pending" if it is due, owed or
  expected later, "Unknown" if the message does not say
- source: the number of the message the transaction came from

Rules:
- Skip messages that are not transactions (greetings, chat, questions).
- One message can contain several transactions: return one item per transaction.
- Do not guess values that are not in the message.
- If there are no transactions, return [].

Messages:
{messages}"""


def parse_json_array(text):
    # remove <think> blocks and ``` fences, then take the outermost [...]
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON array in LLM output: {text[:200]}")
    return json.loads(text[start:end + 1])


def to_row(t, timestamp, content):
    amount = int(t.amount) if t.amount.is_integer() else t.amount
    return [
        t.shop_name, t.client_name, t.location, t.transaction_id, str(amount),
        t.bank_account, t.gst_number, t.status,
        timestamp, content,  # filled by Python from the source message, not by the LLM
    ]


async def extract(messages, llm=None, batch_size=EXTRACT_BATCH_SIZE) -> list[list[str]]:
    # keep only messages that have text (a media message without caption has none)
    parsed = [parse_message(m) for m in messages]
    parsed = [(ts, content) for ts, content in parsed if content]
    if not parsed:
        return []

    # no tools, only text in -> JSON out; max_retries waits out Groq's per-minute 429s (Retry-After)
    llm = llm or ChatGroq(model=GROQ_MODEL, temperature=0, max_retries=6)

    # small chunks: one big call can exceed the free tier's output tokens per minute and then never succeeds.
    # any chunk failing raises, so the run marks nothing and the next run retries all of them
    rows = []
    for start in range(0, len(parsed), batch_size):
        rows += await extract_chunk(parsed[start:start + batch_size], llm)
    return rows


async def extract_chunk(parsed, llm) -> list[list[str]]:
    numbered = "\n".join(f"{i}. {content}" for i, (_, content) in enumerate(parsed, 1))
    prompt = PROMPT.format(messages=numbered)

    error = None
    for attempt in range(2):
        text = prompt if error is None else (
            f"{prompt}\n\nYour previous answer was invalid: {error}\nReturn only the corrected JSON array."
        )
        response = await llm.ainvoke(text)
        try:
            transactions = TRANSACTIONS.validate_python(parse_json_array(response.content))
            bad = [t.source for t in transactions if not 1 <= t.source <= len(parsed)]
            if bad:
                raise ValueError(f"source must be between 1 and {len(parsed)}, got {bad}")
            return [to_row(t, *parsed[t.source - 1]) for t in transactions]
        except ValueError as e:  # json and pydantic errors are ValueErrors too
            error = str(e)[:500]
            print(f"extract attempt {attempt + 1} failed: {error}")

    raise RuntimeError(f"LLM output still invalid after retry: {error}")
