"""Catch canvas screenshots posted from a browser page and write them to disk.

The game renders into a canvas, so there is nothing in the DOM to look at. This
gives the page somewhere to send `canvas.toDataURL()` while a human (or an
agent) is looking at the game, so the rendering can be checked as an image
rather than guessed at from the code.

    python tools/patchman_shotcatch.py [port] [outdir]

Then, in the page:

    fetch('http://127.0.0.1:8078/shot?name=title', {
      method: 'POST', body: document.querySelector('canvas').toDataURL()
    })

Development only. It binds to loopback and writes only into its output folder.
"""

import base64
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8078
OUTDIR = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "_shots")
os.makedirs(OUTDIR, exist_ok=True)


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        name = "shot"
        if "?" in self.path:
            for part in self.path.split("?", 1)[1].split("&"):
                if part.startswith("name="):
                    name = part[5:]
        # Only a plain filename, never a path.
        name = "".join(c for c in name if c.isalnum() or c in "-_")[:60] or "shot"

        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        text = body.decode("utf-8", "replace")
        if "," in text:
            text = text.split(",", 1)[1]
        path = os.path.join(OUTDIR, name + ".png")
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(text))

        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(("saved " + path).encode())
        print("saved", path, "(%d bytes)" % os.path.getsize(path), flush=True)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print("catching shots on http://127.0.0.1:%d -> %s" % (PORT, OUTDIR), flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
