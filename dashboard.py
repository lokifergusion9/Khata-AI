import asyncio
import csv
from datetime import date, datetime
import altair as alt
import pandas as pd
import streamlit as st
from core.config import DEMO_LEDGER, DEMO_MODE, DRY_RUN, load_tools
from core.db import count_processed
from core.graph import run_pipeline
from core.lock import PipelineBusy
from core.sheets import HEADERS, read_ledger
from summary import compute_stats, format_inr, to_amount


async def fetch_rows():
    # the dashboard only reads the Sheet, so start just that MCP server
    tools = await load_tools(["google-sheets"])
    return await read_ledger(tools)


def demo_rows() -> list[list[str]]:
    # fictional ledger in the same 10-column shape the Sheet returns (header row skipped)
    with open(DEMO_LEDGER, encoding="utf-8", newline="") as f:
        return list(csv.reader(f))[1:]


@st.cache_data(ttl=60)
def load_ledger() -> pd.DataFrame:
    rows = demo_rows() if DEMO_MODE else asyncio.run(fetch_rows())
    df = pd.DataFrame(rows, columns=HEADERS)
    df["Amount"] = df["Amount"].map(to_amount)
    df["Date & Time"] = pd.to_datetime(df["Date & Time"], format="%Y-%m-%d %H:%M:%S", errors="coerce")
    # same rule as the WhatsApp summary: Client Name is often empty, so fall back to the shop name
    df["Client"] = df["Client Name"].where(df["Client Name"] != "", df["Shop Name"]).replace("", "Unknown")
    return df


CHART_COLOR = "#2a78d6"  # a single series uses one hue (categorical slot 1)


def amount_bars(data: pd.DataFrame, category: str, horizontal: bool = False) -> alt.Chart:
    # thin bars (a lone bar must not fill the chart), 4px rounded data end, ₹ tooltip
    size = max(6, min(28, 300 // max(len(data), 1)))
    amount = alt.X("Amount:Q", title="Amount (₹)") if horizontal else alt.Y("Amount:Q", title="Amount (₹)")
    labels = (
        alt.Y(f"{category}:N", sort="-x", title=None) if horizontal
        else alt.X(f"{category}:N", sort=None, title=None, axis=alt.Axis(labelAngle=0))
    )
    x, y = (amount, labels) if horizontal else (labels, amount)
    return (
        alt.Chart(data)
        .mark_bar(color=CHART_COLOR, size=size, cornerRadiusEnd=4)
        .encode(x=x, y=y, tooltip=[alt.Tooltip(f"{category}:N"), alt.Tooltip("Amount:Q", title="Amount (₹)", format=",.0f")])
        .properties(height=260)
    )


def run_now() -> tuple[str, str]:
    # runs the same ingestion graph as client.py; returns (message type, text) for the sidebar
    try:
        result = asyncio.run(run_pipeline())
    except PipelineBusy:
        return "warning", "Another run is in progress (probably the scheduled task). Try again in a minute."
    except Exception as e:
        return "error", f"Run failed, nothing marked: {e}"
    if result.get("error"):
        return "error", f"Run failed, nothing marked: {result['error']}"
    if not result.get("new_messages"):
        return "info", "No new messages."
    written = f"{result.get('rows_written', 0)} rows {'printed (dry run)' if DRY_RUN else 'written'}"
    return "success", f"{len(result['new_messages'])} new messages → {written}"


def apply_filters(df: pd.DataFrame, start: date, end: date, statuses: list, clients: list) -> pd.DataFrame:
    # an empty status/client selection means "all"
    day = df["Date & Time"].dt.date
    mask = (day >= start) & (day <= end)
    if statuses:
        mask &= df["Payment Status"].isin(statuses)
    if clients:
        mask &= df["Client"].isin(clients)
    return df[mask]


st.set_page_config(page_title="Finance Ledger", page_icon="📊", layout="wide")
st.title("📊 Finance Ledger")
if DEMO_MODE:
    st.info(
        "**Live demo on fictional sample data.** In production this dashboard reads the Google Sheet "
        "that KhataAI fills from WhatsApp payment messages. "
        "[How it works →](https://github.com/lokifergusion9/mcp-financial-automation)"
    )

try:
    ledger = load_ledger()
except Exception as e:
    st.error(f"Could not load the ledger: {e}")
    st.stop()

if ledger.empty:
    st.info("The ledger has no transactions yet.")
    st.stop()

# ---------- sidebar filters ----------
with st.sidebar:
    st.header("Filters")
    first_day, last_day = ledger["Date & Time"].min().date(), ledger["Date & Time"].max().date()
    picked = st.date_input("Date range", value=(first_day, last_day), min_value=first_day, max_value=last_day)
    # while the user is picking, date_input returns only the start date
    start, end = (picked[0], picked[-1]) if isinstance(picked, tuple) and picked else (first_day, last_day)

    statuses = st.multiselect("Payment status", sorted(ledger["Payment Status"].unique()), placeholder="All")
    clients = st.multiselect("Client", sorted(ledger["Client"].unique()), placeholder="All")

    st.divider()
    if DEMO_MODE:
        st.button("▶ Run pipeline now", width="stretch", disabled=True, help="Needs the private WhatsApp bridge; off in the demo")
    elif st.button("▶ Run pipeline now", width="stretch", help="Fetch new WhatsApp messages and write them to the Sheet"):
        with st.spinner("Running: WhatsApp → LLM → Sheet …"):
            st.session_state["last_run"] = run_now()
        load_ledger.clear()  # show the new rows right away instead of after the 60s cache
        st.rerun()
    if "last_run" in st.session_state:
        kind, text = st.session_state["last_run"]
        getattr(st, kind)(text)  # st.success / st.info / st.warning / st.error

filtered = apply_filters(ledger, start, end, statuses, clients)

# ---------- KPI row: same compute_stats() as the WhatsApp summary, so the numbers always match ----------
stats = compute_stats(filtered[HEADERS].values.tolist())
total_col, received_col, pending_col, count_col = st.columns(4)
total_col.metric("Total", format_inr(stats["total"]))
received_col.metric(f"Received ({stats['received_count']})", format_inr(stats["received"]))
pending_col.metric(f"Pending ({stats['pending_count']})", format_inr(stats["pending"]))
count_col.metric("Transactions", stats["count"])
processed = ledger["Original Message"].nunique() if DEMO_MODE else count_processed()
st.caption(f"📨 {processed} WhatsApp messages processed so far (all time, not filtered)")

if filtered.empty:
    st.info("No transactions match these filters.")
    st.stop()

# ---------- charts: one series each, so one hue and no legend (the subheader names it) ----------
by_day = filtered.groupby(filtered["Date & Time"].dt.date)["Amount"].sum().reset_index()
by_day["Day"] = pd.to_datetime(by_day["Date & Time"]).dt.strftime("%d %b")  # already in date order
by_client = filtered.groupby("Client", as_index=False)["Amount"].sum()

day_col, client_col = st.columns(2)
with day_col:
    st.subheader("Amount by day")
    st.altair_chart(amount_bars(by_day, "Day"), width="stretch")
with client_col:
    st.subheader("Amount by client")
    # horizontal: client names are long; largest first
    st.altair_chart(amount_bars(by_client, "Client", horizontal=True), width="stretch")

# ---------- ledger table: newest first, useful columns first ----------
st.subheader("Transactions")
st.caption(
    f"Showing {len(filtered)} of {len(ledger)} transactions · "
    f"refreshed every 60s · page loaded {datetime.now():%H:%M:%S}"
)
st.dataframe(
    filtered.sort_values("Date & Time", ascending=False),
    hide_index=True,
    width="stretch",
    column_order=[
        "Date & Time", "Client", "Amount", "Payment Status", "Location", "Transaction ID",
        "Shop Name", "Bank Account", "GST Number", "Original Message",
    ],
    column_config={
        "Date & Time": st.column_config.DatetimeColumn("Date & Time", format="DD MMM YYYY, HH:mm"),
        "Amount": st.column_config.NumberColumn("Amount (₹)", format="localized"),
    },
)
