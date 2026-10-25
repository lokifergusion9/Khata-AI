from dataclasses import dataclass
from typing import TypedDict
from langchain_core.tools import BaseTool
from langgraph.graph import StateGraph, START, END
from langgraph.runtime import Runtime
from . import db, extractor, sheets, whatsapp
from .config import DRY_RUN, load_tools
from .lock import pipeline_lock


class PipelineState(TypedDict, total=False):
    groups: dict[str, dict]          # jid -> {"name": group name}
    raw_messages: list[str]          # every fetched message, "[ts] Chat: X From: Y: text"
    new_messages: list[str]          # only the ones not processed before
    transactions: list[list[str]]    # validated rows, 10 columns each
    rows_written: int
    error: str | None


@dataclass
class Context:
    # MCP tools are passed per run, not stored in the state (the state holds data only)
    tools: dict[str, BaseTool]


# ---------- nodes: each one calls a Phase 1 function and returns only the keys it changes ----------

async def discover_groups(state: PipelineState, runtime: Runtime[Context]):
    groups = await whatsapp.discover_groups(runtime.context.tools)
    return {"groups": groups}


async def fetch_messages(state: PipelineState, runtime: Runtime[Context]):
    raw = await whatsapp.get_messages_from_groups(state["groups"], runtime.context.tools)
    split = await whatsapp.split_messages_by_group(raw)
    messages = [msg for group_data in split.values() for msg in group_data["messages"]]
    return {"raw_messages": messages}


async def filter_new(state: PipelineState, runtime: Runtime[Context]):
    unique = list(dict.fromkeys(state["raw_messages"]))
    return {"new_messages": [msg for msg in unique if not db.is_msg_exist(msg)]}


async def extract_transactions(state: PipelineState, runtime: Runtime[Context]):
    try:
        rows = await extractor.extract(state["new_messages"])
    except Exception as e:
        # do not crash: the router sends this to END, so nothing gets marked
        return {"error": f"extract failed: {e}"}
    return {"transactions": rows}


async def write_to_sheet(state: PipelineState, runtime: Runtime[Context]):
    try:
        written = await sheets.append_rows(runtime.context.tools, state["transactions"])
    except Exception as e:
        return {"error": f"write failed: {e}"}
    return {"rows_written": written}


async def mark_processed(state: PipelineState, runtime: Runtime[Context]):
    # in a dry run nothing was written, so do not mark anything either
    if DRY_RUN:
        print("DRY RUN, messages not marked as processed")
        return {}
    for msg in state["new_messages"]:
        db.save_msg(msg)
    return {}


# ---------- routing: plain functions that look at the state and return the next node ----------

def route_after_filter(state: PipelineState):
    return "extract_transactions" if state["new_messages"] else END


def route_after_extract(state: PipelineState):
    if state.get("error"):
        return END                      # nothing marked, the next run retries
    if not state["transactions"]:
        return "mark_processed"         # e.g. only "good morning" messages
    return "write_to_sheet"


def route_after_write(state: PipelineState):
    return END if state.get("error") else "mark_processed"


def build_graph():
    builder = StateGraph(PipelineState, context_schema=Context)

    builder.add_node("discover_groups", discover_groups)
    builder.add_node("fetch_messages", fetch_messages)
    builder.add_node("filter_new", filter_new)
    builder.add_node("extract_transactions", extract_transactions)
    builder.add_node("write_to_sheet", write_to_sheet)
    builder.add_node("mark_processed", mark_processed)

    builder.add_edge(START, "discover_groups")
    builder.add_edge("discover_groups", "fetch_messages")
    builder.add_edge("fetch_messages", "filter_new")
    builder.add_conditional_edges("filter_new", route_after_filter, ["extract_transactions", END])
    builder.add_conditional_edges("extract_transactions", route_after_extract, ["write_to_sheet", "mark_processed", END])
    builder.add_conditional_edges("write_to_sheet", route_after_write, ["mark_processed", END])
    builder.add_edge("mark_processed", END)

    return builder.compile()


graph = build_graph()


async def run_pipeline() -> PipelineState:
    # shared by client.py and the dashboard button; raises PipelineBusy if a run is already going
    with pipeline_lock():
        tools = await load_tools()
        return await graph.ainvoke({}, context=Context(tools=tools))
