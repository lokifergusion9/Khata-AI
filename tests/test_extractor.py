import asyncio
from types import SimpleNamespace

import pytest

from core.extractor import Transaction, extract, parse_json_array


class FakeLLM:
    # returns the queued answers in order and records every prompt
    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    async def ainvoke(self, text):
        self.prompts.append(text)
        return SimpleNamespace(content=self.answers.pop(0))


MESSAGES = [
    "[2026-10-25 09:00:05] Chat: Payments From: Ramesh: Good morning team",
    "[2026-10-25 09:15:40] Chat: Payments From: Ramesh: Agarwal Sweets Jodhpur ne ₹18,750 bhej diye",
    "[2026-10-25 10:05:22] Chat: Payments From: Ramesh: [image - Message ID: X1 - Chat JID: 1@g.us]",
]
GOOD = '[{"shop_name": "Agarwal Sweets", "location": "Jodhpur", "amount": 18750, "status": "Received", "source": 2}]'


def test_parse_json_array_strips_think_block_and_fences():
    text = '<think>the user wants [json]</think>\n```json\n[{"a": 1}]\n```'
    assert parse_json_array(text) == [{"a": 1}]


def test_parse_json_array_without_array_raises():
    with pytest.raises(ValueError):
        parse_json_array("no transactions here")


@pytest.mark.parametrize("raw, expected", [("₹15,000", 15000), ("Rs. 2,500", 2500), ("Rs 99.5", 99.5), (750, 750)])
def test_amount_is_cleaned(raw, expected):
    assert Transaction(amount=raw, source=1).amount == expected


def test_rows_take_timestamp_and_text_from_the_source_message():
    llm = FakeLLM(GOOD)
    rows = asyncio.run(extract(MESSAGES, llm=llm))
    assert rows == [[
        "Agarwal Sweets", "", "Jodhpur", "", "18750", "", "", "Received",
        "2026-10-25 09:15:40", "Agarwal Sweets Jodhpur ne ₹18,750 bhej diye",
    ]]


def test_media_without_caption_is_not_sent_to_the_llm():
    llm = FakeLLM(GOOD)
    asyncio.run(extract(MESSAGES, llm=llm))
    assert "Message ID" not in llm.prompts[0]
    assert "2. Agarwal Sweets" in llm.prompts[0]


def test_no_text_messages_skip_the_llm():
    llm = FakeLLM()
    assert asyncio.run(extract([MESSAGES[2]], llm=llm)) == []
    assert llm.prompts == []


def test_invalid_output_is_repaired_once():
    llm = FakeLLM("sorry, here you go: {", GOOD)
    rows = asyncio.run(extract(MESSAGES, llm=llm))
    assert len(rows) == 1
    assert "previous answer was invalid" in llm.prompts[1]


def test_out_of_range_source_is_rejected():
    bad = GOOD.replace('"source": 2', '"source": 9')
    llm = FakeLLM(bad, GOOD)
    asyncio.run(extract(MESSAGES, llm=llm))
    assert "source must be between 1 and 2" in llm.prompts[1]


def test_messages_are_sent_in_chunks_and_sources_map_per_chunk():
    messages = [f"[2026-10-25 09:00:0{i}] Chat: P From: R: Shop{i} 100{i} received" for i in range(5)]
    second = '[{"shop_name": "x", "amount": 1, "status": "Received", "source": 2}]'
    first = second.replace('"source": 2', '"source": 1')
    llm = FakeLLM(second, second, first)
    rows = asyncio.run(extract(messages, llm=llm, batch_size=2))
    assert len(llm.prompts) == 3
    # sources are numbered inside each chunk: chunk [Shop0, Shop1] source 2 is Shop1, the last chunk is [Shop4]
    assert "1. Shop4" in llm.prompts[2]
    assert [row[9] for row in rows] == ["Shop1 1001 received", "Shop3 1003 received", "Shop4 1004 received"]


def test_a_failing_chunk_fails_the_whole_run():
    messages = [f"[2026-10-25 09:00:0{i}] Chat: P From: R: Shop{i} 100{i} received" for i in range(4)]
    ok = '[{"shop_name": "x", "amount": 1, "status": "Received", "source": 1}]'
    llm = FakeLLM(ok, "bad", "still bad")
    with pytest.raises(RuntimeError):
        asyncio.run(extract(messages, llm=llm, batch_size=2))


def test_still_invalid_after_retry_raises():
    llm = FakeLLM("nope", "still nope")
    with pytest.raises(RuntimeError):
        asyncio.run(extract(MESSAGES, llm=llm))
