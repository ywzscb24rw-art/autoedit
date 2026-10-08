"""Desktop entry point: the API plus the built web UI on 127.0.0.1, locked to one launch token.

    python -m server.app --token <random> [--port N] [--web path/to/web/dist]

The Electron shell starts this, reads the "AUTOEDIT_PORT=<n>" line it prints, and opens a
window at /?token=<random>. The first request swaps the token for a strict, same-site,
HTTP-only cookie, so every later request (fetches and <video> tags alike) is authorised
without the UI handling the token, while web pages from other sites and other local
programs can't use the API (and with it, the tester's Claude credits).
"""

import argparse
import hmac
import os
import socket
from pathlib import Path

# Hugging Face's Xet transfer writes the model file only when it's complete, which leaves the
# setup screen's progress bar stuck at 0% for minutes. Plain HTTP writes as it downloads.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

COOKIE = "autoedit_session"
OPEN_PATHS = {"/api/health"}  # Electron polls this before opening the window


def install_token_guard(app, token: str) -> None:
    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.url.path in OPEN_PATHS:
            return await call_next(request)
        query = request.query_params.get("token")
        if query is not None and hmac.compare_digest(query, token):
            resp = RedirectResponse(request.url.path or "/")
            resp.set_cookie(COOKIE, token, httponly=True, samesite="strict")
            return resp
        if hmac.compare_digest(request.cookies.get(COOKIE, ""), token):
            return await call_next(request)
        return JSONResponse({"detail": "not authorised"}, status_code=403)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def build(token: str, web_dist: Path):
    from .main import app

    install_token_guard(app, token)
    if web_dist.is_dir():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
    return app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--web", type=Path, default=Path(__file__).resolve().parent.parent / "web" / "dist")
    args = ap.parse_args()

    import uvicorn

    app = build(args.token, args.web)
    port = args.port or free_port()
    print(f"AUTOEDIT_PORT={port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()

