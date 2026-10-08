"""On-disk project storage: one folder per project, plain JSON files, no database."""

import json
import os
import threading
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.environ.get("AUTOEDIT_DATA", ROOT / "data" / "projects"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

_locks: dict[str, threading.Lock] = {}


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


class Project:
    def __init__(self, project_id: str):
        self.id = project_id
        self.dir = DATA_DIR / project_id
        if not self.dir.is_dir():
            raise FileNotFoundError(f"project {project_id} not found")
        self._lock = _locks.setdefault(project_id, threading.Lock())

    # ---- creation / lookup ----
    @classmethod
    def create(cls, name: str) -> "Project":
        pid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        d = DATA_DIR / pid
        (d / "outputs").mkdir(parents=True)
        _write_json(d / "state.json", {
            "id": pid, "name": name, "created": time.time(),
            "status": "new", "stage": None, "progress": 0.0, "error": None,
        })
        return cls(pid)

    @classmethod
    def all(cls) -> list["Project"]:
        out = []
        for d in sorted(DATA_DIR.iterdir(), reverse=True):
            if (d / "state.json").exists():
                out.append(cls(d.name))
        return out

    # ---- paths ----
    @property
    def raw(self) -> Path | None:
        found = sorted(self.dir.glob("raw.*"))
        return found[0] if found else None

    source = property(lambda self: self.dir / "source.mp4")
    proxy = property(lambda self: self.dir / "proxy.mp4")
    audio = property(lambda self: self.dir / "audio.wav")
    transcript_path = property(lambda self: self.dir / "transcript.json")
    edits_path = property(lambda self: self.dir / "edits.json")
    outputs = property(lambda self: self.dir / "outputs")

    @property
    def preview_source(self) -> Path:
        """The fast 1080p proxy when there is one, else the source."""
        return self.proxy if self.proxy.exists() else self.source

    # ---- json helpers ----
    def read(self, name: str, default=None):
        p = self.dir / name
        return json.loads(p.read_text()) if p.exists() else default

    def write(self, name: str, data) -> None:
        with self._lock:
            _write_json(self.dir / name, data)

    @property
    def state(self) -> dict:
        return self.read("state.json")

    def update_state(self, **kw) -> dict:
        with self._lock:
            s = json.loads((self.dir / "state.json").read_text())
            s.update(kw)
            _write_json(self.dir / "state.json", s)
            return s
