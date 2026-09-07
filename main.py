"""Run the local comparison viewer: uv run python main.py."""

import argparse

import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run("comparison.server:app", host=args.host, port=args.port, access_log=False)
