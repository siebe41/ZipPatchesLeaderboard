"""Container entrypoint for the no-build setup.

Why Python instead of a shell script:
  * The legacy Synology Docker app's Command field splits on spaces and ignores
    quotes, which breaks an inline `sh -c "..."`.
  * Shell scripts also break if the file picks up Windows CRLF line endings when
    edited/uploaded from Windows (`set: Illegal option -`).
Python sidesteps both: the container command is just `python /app/start.py`
(two tokens, no quoting), and Python's source parser handles CRLF fine.
"""
import os
import subprocess
import sys

APP_DIR = "/app"


def main():
    os.chdir(APP_DIR)
    req = os.path.join(APP_DIR, "requirements.txt")
    if os.path.exists(req):
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-cache-dir", "-r", req],
            check=True,
        )
    # Replace this process with uvicorn so signals (stop/restart) propagate.
    os.execvp(
        sys.executable,
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", "0.0.0.0", "--port", "8000"],
    )


if __name__ == "__main__":
    main()
