import json
import re
from .config import MESSAGES_PER_GROUP, SALES_KEYWORDS, USE_SAMPLE_DATA, SAMPLE_FILE, tool_text


def load_sample_groups():
    return json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))["groups"]


async def discover_groups(tool_names):
    if USE_SAMPLE_DATA:
        groups = {g["jid"]: {"name": g["name"]} for g in load_sample_groups()}
        for group in groups.values():
            print(f"Added (sample): {group['name']}")
        return groups

    chats = await tool_names["list_chats"].ainvoke({"limit": 100})
    monitored_groups = {}
    for chat in chats:
        data = json.loads(chat["text"])

        jid = data["jid"]

        group_name = data["name"]

        if not jid.endswith("@g.us"):
            continue

        group_name_lower = group_name.lower()

        # keyword check
        if any(keyword in group_name_lower for keyword in SALES_KEYWORDS):
            monitored_groups[jid] = {"name": group_name}
            print(f"Added: {group_name}")

    return monitored_groups


async def get_messages_from_groups(known_groups, tool_names):
    if USE_SAMPLE_DATA:
        # same raw text shape as list_messages, so the split/parse code is tested too
        return {
            g["jid"]: {"group_name": g["name"], "messages": "\n".join(g["messages"])}
            for g in load_sample_groups() if g["jid"] in known_groups
        }

    messages = {}
    for jid, group in known_groups.items():
        # include_context=False, otherwise every message comes back 2-3 times
        chat_msg = await tool_names["list_messages"].ainvoke(
            {"chat_jid": jid, "limit": MESSAGES_PER_GROUP, "include_context": False}
        )
        msg = tool_text(chat_msg)
        messages[jid] = {"group_name": group["name"], "messages": msg}

    return messages


async def split_messages_by_group(messages):
    split_data = {}
    pattern = r'(?=\[\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\])'

    for jid, group_data in messages.items():
        raw_text = group_data["messages"]
        msg_list = re.split(pattern, raw_text)
        msg_list = [msg.strip() for msg in msg_list if msg.strip()]

        split_data[jid] = {
            "group_name": group_data["group_name"],
            "messages": msg_list
        }

    return split_data


MESSAGE_PATTERN = re.compile(
    r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (?:Chat: .*? )?From: .*?: (.*)$', re.DOTALL
)
MEDIA_PATTERN = re.compile(r'^\[\w+ - Message ID: [^\]]*\]\s*')


def parse_message(msg):
    # "[2026-10-25 11:09:33] Chat: X From: Me: text" -> ("2026-10-25 11:09:33", "text")
    match = MESSAGE_PATTERN.match(msg)
    if not match:
        return "", msg
    timestamp, content = match.groups()
    # media messages start with "[image - Message ID: ...]", keep only the caption (if any)
    content = MEDIA_PATTERN.sub("", content).strip()
    return timestamp, content


async def send_message(tool_names, chat_jid, text):
    result = await tool_names["send_message"].ainvoke({"recipient": chat_jid, "message": text})
    return json.loads(tool_text(result))
