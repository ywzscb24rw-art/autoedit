"""Per-user settings for the desktop app: the tester's Anthropic API key and model choice.

Stored as JSON in the app's support folder with owner-only permissions (0600). The Keychain
isn't used on purpose: an unsigned app's identity changes with every build, so macOS would
ask "allow access?" after each update.

In development, ANTHROPIC_API_KEY / CLAUDE_MODEL from .env still work as fallbacks.
"""

import json
import os
from pathlib import Path

MODELS = {
    "claude-sonnet-5-5": "Claude Sonnet 5.5 (recommended: about half the cost)",
    "claude-opus-5-5": "Claude Opus 5.5 (best judgment for clips)",
}
DEFAULT_MODEL = "claude-sonnet-5-5"


def config_path() -> Path:
    base = os.environ.get("AUTOEDIT_CONFIG")
    if base:
        return Path(base)
    return Path.home() / "Library" / "Application Support" / "AutoEdit" / "config.json"


def load() -> dict:
    p = config_path()
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(updates: dict) -> dict:
    data = {**load(), **updates}
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    # Create the file owner-only before writing the key into it.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(p)
    os.chmod(p, 0o600)
    return data


def api_key() -> str | None:
    return load().get("api_key") or os.environ.get("ANTHROPIC_API_KEY") or None


def model() -> str:
    m = load().get("model") or os.environ.get("CLAUDE_MODEL") or DEFAULT_MODEL
    return m


def public() -> dict:
    """Settings safe to show in the UI: the key is masked."""
    key = api_key()
    return {
        "has_key": bool(key),
        "key_hint": f"{key[:10]}…{key[-4:]}" if key and len(key) > 16 else None,
        "model": model(),
        "models": MODELS,
    }
