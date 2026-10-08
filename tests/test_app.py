import json
import os
import stat

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import app as desktop
from server import errors, settings


def _client(token="secret"):
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/projects")
    def projects():
        return []

    desktop.install_token_guard(app, token)
    return TestClient(app)


def test_token_guard_rejects_requests_without_the_session():
    c = _client()
    assert c.get("/api/projects").status_code == 403
    assert c.get("/api/projects", cookies={desktop.COOKIE: "wrong"}).status_code == 403


def test_launch_token_becomes_a_strict_httponly_cookie():
    c = _client()
    r = c.get("/?token=secret", follow_redirects=False)
    assert r.status_code == 307
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert c.get("/api/projects").status_code == 200  # the client now carries the cookie


def test_health_is_open_for_the_launcher():
    assert _client().get("/api/health").status_code == 200


def test_settings_file_is_owner_only_and_key_is_masked(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOEDIT_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings.save({"api_key": "sk-ant-api03-abcdefghijklmnop-WXYZ", "model": "claude-opus-5-5"})
    mode = stat.S_IMODE(os.stat(tmp_path / "config.json").st_mode)
    assert mode == 0o600
    pub = settings.public()
    assert pub["has_key"] and pub["model"] == "claude-opus-5-5"
    assert "abcdefghijklmnop" not in json.dumps(pub)


def test_friendly_errors():
    import anthropic
    import httpx

    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    def err(cls, status, msg):
        return cls(msg, response=httpx.Response(status, request=req), body=None)

    assert "rejected" in errors.friendly(err(anthropic.AuthenticationError, 401, "invalid x-api-key"))
    assert "out of credits" in errors.friendly(err(anthropic.BadRequestError, 400, "Your credit balance is too low"))
    from server.pipeline.ingest import FFmpegError
    assert "Couldn't process this video" in errors.friendly(FFmpegError("ffmpeg failed: moov atom not found"))
    assert errors.friendly(RuntimeError("Nothing left to render.")) == "Nothing left to render."
