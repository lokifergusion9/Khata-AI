import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# core.config reads these at import time; the code under test does not use them
for name in ("GOOGLE_APPLICATION_CREDENTIALS", "WHATSAPP_SERVER_DIR", "SPREADSHEET_ID"):
    os.environ.setdefault(name, "unused-in-tests")
