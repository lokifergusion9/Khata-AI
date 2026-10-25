# Runs a .cmd script without a console window and returns its exit code, so Task Scheduler
# shows failures. Started with pythonw.exe (no window of its own):
#   pythonw.exe run_hidden.pyw C:\path\to\script.cmd
import subprocess
import sys

sys.exit(subprocess.call(["cmd.exe", "/c", sys.argv[1]], creationflags=subprocess.CREATE_NO_WINDOW))
