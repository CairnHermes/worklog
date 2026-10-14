#!/usr/bin/env python
"""Proof-of-concept launcher for the Responsive Web App Development job.

The app itself is a single, dependency-free, responsive HTML file at
PoC/index.html (open it directly in any browser, or serve it below).

This script does two things so the PoC is "minimal but real and runnable":

  1) Starts a tiny local HTTP server on a free port pointing at the PoC
     folder and prints the URL to open.
  2) Runs the Playwright verification (verify.mjs) that proves the app:
       - renders correctly,
       - is responsive at mobile / tablet / desktop widths,
       - rejects an empty title with an inline error,
       - accepts a valid task and shows it on the board with updated stats.

Usage:
    python PoC.py            # verify then leave the preview server running
    python PoC.py --verify   # verify and exit (no server)
"""

from __future__ import annotations

import http.server
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
POC_DIR = HERE / "PoC"
VERIFY = HERE / "verify.mjs"


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def run_verify() -> int:
    print("[PoC] running Playwright verification (verify.mjs) ...")
    print("$ " + "node verify.mjs")
    rc = subprocess.call(["node", "verify.mjs"], cwd=str(HERE))
    return rc


def serve() -> None:
    port = _free_port()
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(POC_DIR), **k)  # noqa: E731
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{port}/index.html"
    print(f"[PoC] preview server -> {url}")
    print("[PoC] press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[PoC] stopped.")


if __name__ == "__main__":
    verify_only = "--verify" in sys.argv
    rc = run_verify()
    if rc != 0:
        print("[PoC] verification failed; not starting server.")
        sys.exit(rc)
    if verify_only:
        print("[PoC] verification passed. Opening the file directly works too:")
        print(f"[PoC]   {POC_DIR / 'index.html'}")
    else:
        serve()
