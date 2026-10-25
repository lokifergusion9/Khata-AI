import asyncio
import sys
from core.config import DRY_RUN, USE_SAMPLE_DATA
from core.graph import run_pipeline
from core.lock import PipelineBusy


def print_report(result):
    if result.get("error"):
        status = "ERROR, nothing marked, the next run retries"
    elif not result.get("new_messages"):
        status = "no new messages found"
    else:
        status = "ok"

    print("\n----- run report -----")
    print(f"groups found     : {len(result.get('groups', {}))}")
    print(f"messages fetched : {len(result.get('raw_messages', []))}")
    print(f"new messages     : {len(result.get('new_messages', []))}")
    print(f"transactions     : {len(result.get('transactions', []))}")
    print(f"rows written     : {result.get('rows_written', 0)}{' (dry run)' if DRY_RUN else ''}")
    print(f"error            : {result.get('error') or '-'}")
    print(f"status           : {status}")


async def main():
    if USE_SAMPLE_DATA or DRY_RUN:
        print(f"mode: USE_SAMPLE_DATA={USE_SAMPLE_DATA} DRY_RUN={DRY_RUN}")

    try:
        result = await run_pipeline()
    except PipelineBusy as e:
        # not a failure: the other run (scheduler or dashboard button) handles the new messages
        print(f"skipped: {e}")
        return {}
    print_report(result)
    return result


if __name__ == "__main__":
    # ₹ in messages must print even when output is redirected (e.g. from Task Scheduler)
    sys.stdout.reconfigure(encoding="utf-8")

    result = asyncio.run(main())
    # non-zero exit code, so Task Scheduler shows the run as failed
    if result.get("error"):
        raise SystemExit(1)
