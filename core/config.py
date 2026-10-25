import os
from pathlib import Path
from dotenv import load_dotenv
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient


# paths are built from the project root, so it works from any working directory
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
load_dotenv(ROOT / ".env")

GROQ_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
GOOGLE_APPLICATION_CREDENTIALS = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
WHATSAPP_SERVER_DIR = os.environ["WHATSAPP_SERVER_DIR"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
SHEET_NAME = os.getenv("SHEET_NAME", "Sheet1")
MESSAGES_PER_GROUP = int(os.getenv("MESSAGES_PER_GROUP", "20"))
SUMMARY_CHAT_JID = os.getenv("SUMMARY_CHAT_JID", "")

# offline development: read messages from a file / print rows instead of writing them
USE_SAMPLE_DATA = os.getenv("USE_SAMPLE_DATA", "false").lower() == "true"
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
SAMPLE_FILE = DATA_DIR / "sample_messages.json"
DB_FILE = DATA_DIR / "processed_msg.db"

SERVERS = {
    "google-sheets": {
        "transport": "stdio",
        "command": "uvx",
        # mcp-google-sheets crashes with mcp 2.x (FastMCP was renamed)
        "args": ["--with", "mcp<2", "mcp-google-sheets@latest"],
        "env": {"GOOGLE_APPLICATION_CREDENTIALS": GOOGLE_APPLICATION_CREDENTIALS},
    },
    "whatsapp": {
        "transport": "stdio",
        "command": "uv",
        "args": ["--directory", WHATSAPP_SERVER_DIR, "run", "python", "main.py"],
    },
}

SALES_KEYWORDS = [
    "sales",
    "payment",
    "payments",
    "collection",
    "recovery",
    "finance",
    "accounts",
    "billing",
]


async def load_tools(names: list[str] | None = None) -> dict[str, BaseTool]:
    if names is not None:
        # the caller says exactly which servers it needs, e.g. ["google-sheets"]
        servers = {name: SERVERS[name] for name in names}
    else:
        # skip the servers this mode does not need (sample data -> no WhatsApp, dry run -> no Sheets)
        servers = {
            name: server for name, server in SERVERS.items()
            if not (name == "whatsapp" and USE_SAMPLE_DATA)
            and not (name == "google-sheets" and DRY_RUN)
        }
    if not servers:
        return {}
    client = MultiServerMCPClient(servers)
    tools = await client.get_tools()
    return {tool.name: tool for tool in tools}


def tool_text(result) -> str:
    # MCP tools return a list of {"type": "text", "text": ...} blocks
    if isinstance(result, str):
        return result
    return "".join(block["text"] for block in result if block.get("type") == "text")
