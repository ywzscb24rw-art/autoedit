"""First-run download of the Whisper speech model, with byte-level progress for the setup screen.

huggingface_hub reports progress per file, and one file is almost all of the 1.6 GB, so we
read the expected size up front and watch the cache folder grow instead.
"""

import threading
from pathlib import Path

from .pipeline import transcribe

_lock = threading.Lock()
_state = {"status": "idle", "progress": 0.0, "error": None, "total_bytes": None}


def repo() -> str:
    return transcribe.MLX_REPOS.get(transcribe.MODEL, transcribe.MODEL)


def is_ready() -> bool:
    from huggingface_hub import snapshot_download

    try:
        snapshot_download(repo(), local_files_only=True)
        return True
    except Exception:
        return False


def _cache_dir() -> Path:
    from huggingface_hub.constants import HF_HUB_CACHE

    return Path(HF_HUB_CACHE) / ("models--" + repo().replace("/", "--"))


def _bytes_on_disk() -> int:
    d = _cache_dir() / "blobs"
    return sum(f.stat().st_size for f in d.glob("*") if f.is_file()) if d.exists() else 0


def status() -> dict:
    with _lock:
        s = dict(_state)
    if s["status"] in ("idle", "error") and is_ready():
        s.update(status="ready", progress=1.0)
    return s


def _download() -> None:
    from huggingface_hub import HfApi, snapshot_download

    try:
        info = HfApi().model_info(repo(), files_metadata=True)
        total = sum(f.size or 0 for f in info.siblings) or None
        with _lock:
            _state.update(total_bytes=total)
        done = threading.Event()

        def watch():
            while not done.wait(0.5):
                if total:
                    with _lock:
                        _state["progress"] = min(0.99, _bytes_on_disk() / total)

        threading.Thread(target=watch, daemon=True).start()
        try:
            snapshot_download(repo())
        finally:
            done.set()
        with _lock:
            _state.update(status="ready", progress=1.0, error=None)
    except Exception as e:  # shown on the setup screen
        with _lock:
            _state.update(status="error", error=f"Download failed ({type(e).__name__}). Check your connection and try again.")


def start() -> dict:
    with _lock:
        if _state["status"] == "downloading":
            return dict(_state)
        _state.update(status="downloading", progress=0.0, error=None)
    threading.Thread(target=_download, daemon=True).start()
    return status()


def require_ready() -> None:
    """Called before transcribing, so a job never starts a silent 1.6 GB download."""
    if transcribe.backend() == "mlx" and not is_ready():
        raise RuntimeError("The speech model isn't downloaded yet. Open Settings and click Download speech model.")

