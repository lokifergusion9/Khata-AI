import os
import time
from contextlib import contextmanager
from .config import DATA_DIR

LOCK_FILE = DATA_DIR / "pipeline.lock"
STALE_AFTER = 15 * 60  # a crashed run leaves the file behind; Task Scheduler kills runs after 10 min


class PipelineBusy(Exception):
    pass


@contextmanager
def pipeline_lock():
    # one ingestion run at a time: the scheduled task and the dashboard button could overlap
    # and both write the same new messages -> duplicate ledger rows
    try:
        if time.time() - LOCK_FILE.stat().st_mtime > STALE_AFTER:
            LOCK_FILE.unlink()
    except FileNotFoundError:
        pass

    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)  # fails if the file exists
    except FileExistsError:
        raise PipelineBusy("another ingestion run is in progress")
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)

    try:
        yield
    finally:
        LOCK_FILE.unlink(missing_ok=True)
