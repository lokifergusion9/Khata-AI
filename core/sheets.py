import json
from .config import DRY_RUN, SPREADSHEET_ID, SHEET_NAME, tool_text

HEADERS = [
    "Shop Name", "Client Name", "Location", "Transaction ID", "Amount",
    "Bank Account", "GST Number", "Payment Status", "Date & Time", "Original Message",
]
NUM_COLUMNS = len(HEADERS)  # A..J


async def get_all_rows(tool_names):
    result = await tool_names["get_sheet_data"].ainvoke(
        {"spreadsheet_id": SPREADSHEET_ID, "sheet": SHEET_NAME}
    )
    text = tool_text(result)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError(f"get_sheet_data failed: {text[:200]}")
    # an empty sheet has no "values" key
    return data["valueRanges"][0].get("values", [])


async def read_ledger(tool_names) -> list[list[str]]:
    rows = await get_all_rows(tool_names)
    # Sheets drops trailing empty cells, so pad every row back to 10 columns
    return [row + [""] * (NUM_COLUMNS - len(row)) for row in rows[1:]]


async def append_rows(tool_names, rows) -> int:
    if not rows:
        return 0

    if DRY_RUN:
        print("DRY RUN, rows not written:")
        for row in rows:
            print("  " + " | ".join(row))
        return len(rows)

    n = len(await get_all_rows(tool_names))  # header + existing rows
    first, last = n + 1, n + len(rows)

    result = await tool_names["update_cells"].ainvoke({
        "spreadsheet_id": SPREADSHEET_ID,
        "sheet": SHEET_NAME,
        "range": f"A{first}:J{last}",
        "data": rows,
    })

    # the server may return an error text instead of raising, so check what it wrote
    text = tool_text(result)
    try:
        written = json.loads(text).get("updatedRows", 0)
    except json.JSONDecodeError:
        written = 0
    if written != len(rows):
        raise RuntimeError(f"update_cells wrote {written} of {len(rows)} rows: {text[:200]}")

    return written
