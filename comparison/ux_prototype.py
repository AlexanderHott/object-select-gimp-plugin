"""Throwaway UX study. Run with python3 comparison/ux_prototype.py."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


if __name__ == "__main__":
    root = Path(__file__).parent / "static" / "ux-prototype"
    server = ThreadingHTTPServer(
        ("127.0.0.1", 8766), partial(SimpleHTTPRequestHandler, directory=str(root))
    )
    print("Selection UX prototypes: http://127.0.0.1:8766", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
