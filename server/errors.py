"""Turn exceptions into messages a non-technical tester can act on."""

import anthropic

from .pipeline.ingest import FFmpegError

CREDITS_URL = "https://platform.claude.com/settings/billing"
KEYS_URL = "https://platform.claude.com/settings/keys"


def friendly(e: BaseException) -> str:
    if isinstance(e, anthropic.AuthenticationError):
        return f"Your Claude API key was rejected. Check it in Settings, or create a new one at {KEYS_URL}."
    if isinstance(e, anthropic.PermissionDeniedError):
        return "Your Claude API key isn't allowed to use this model. Try the other model in Settings."
    if isinstance(e, anthropic.BadRequestError) and "credit balance" in str(e).lower():
        return f"Your Anthropic account is out of credits. Add some at {CREDITS_URL}, then click Re-run."
    if isinstance(e, anthropic.RateLimitError):
        return "Claude is rate-limiting this key right now. Wait a minute, then click Re-run."
    if isinstance(e, (anthropic.OverloadedError, anthropic.InternalServerError, anthropic.ServiceUnavailableError)):
        return "Claude is temporarily overloaded. Wait a minute, then click Re-run."
    if isinstance(e, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
        return "Couldn't reach Claude. Check your internet connection, then click Re-run."
    if isinstance(e, FileNotFoundError) and getattr(e, "filename", "") in ("ffmpeg", "ffprobe"):
        return "AutoEdit's video tools are missing. Reinstall the app."
    if isinstance(e, FFmpegError):
        return "Couldn't process this video file. If it plays in QuickTime, use Help → Send feedback so we can look into it."
    if isinstance(e, RuntimeError):
        return str(e)  # our own messages are already written for people
    return f"Something went wrong ({type(e).__name__}). Use Help → Send feedback so we can fix it."
