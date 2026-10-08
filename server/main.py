"""HTTP API for the web app.  Run: .venv/bin/uvicorn server.main:app --reload --port 8000"""

import hashlib
import shutil
import threading
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .pipeline import edl, run
from .project import DATA_DIR, Project

app = FastAPI(title="AutoEdit")


@app.on_event("startup")
def _mark_interrupted_jobs() -> None:
    # Jobs run in threads of this process, so any job still "running" died with the last server.
    for p in Project.all():
        if p.state["status"] == "running":
            p.update_state(status="error", stage=None, error="Interrupted: the server restarted. Re-run to continue.")


def fingerprint(path: Path) -> str:
    """Size plus a hash of the first and last MiB: cheap, and enough to spot a re-upload."""
    size = path.stat().st_size
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(1 << 20))
        f.seek(max(0, size - (1 << 20)))
        h.update(f.read(1 << 20))
    return h.hexdigest()


def _get(pid: str) -> Project:
    try:
        return Project(pid)
    except FileNotFoundError:
        raise HTTPException(404, "project not found")


def _start(p: Project, target, *args) -> None:
    if p.state["status"] == "running":
        raise HTTPException(409, "a job is already running for this project")
    p.update_state(status="running", stage="queued", progress=0.0, error=None)
    threading.Thread(target=target, args=(p, *args), daemon=True).start()


@app.get("/api/projects")
def list_projects():
    return [p.state for p in Project.all()]


@app.post("/api/projects")
async def create_project(file: UploadFile = File(...), name: str = Form("Untitled recording")):
    ext = (file.filename or "rec.webm").rsplit(".", 1)[-1].lower()
    p = Project.create(name)
    raw = p.dir / f"raw.{ext}"
    with open(raw, "wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)
    fp = fingerprint(raw)
    # Uploading the same file again reuses the existing project and its cached conversion
    # and transcript, instead of processing a second copy.
    for other in Project.all():
        if other.id == p.id or other.raw is None:
            continue
        other_fp = other.state.get("fingerprint")
        if other_fp is None and other.raw.stat().st_size == raw.stat().st_size:
            other_fp = fingerprint(other.raw)
            other.update_state(fingerprint=other_fp)
        if other_fp == fp:
            shutil.rmtree(p.dir)
            return {**other.state, "duplicate": True}
    return p.update_state(fingerprint=fp)


class ProcessReq(BaseModel):
    mode: Literal["clean", "clips"] = "clean"
    use_ai: bool = True
    vertical: bool = False
    punch_in: bool | None = None  # None: the content type's default
    captions: bool | None = None
    content: Literal["screen", "talking", "vlog"] = "screen"
    min_s: float | None = None  # None: the content type's default clip length
    max_s: float | None = None
    max_clips: int = 5


@app.post("/api/projects/{pid}/process")
def process(pid: str, req: ProcessReq):
    p = _get(pid)
    opts = req.model_dump(exclude={"mode", "use_ai"})
    _start(p, run.process, req.mode, req.use_ai, opts)
    return p.state


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    p = _get(pid)
    transcript = p.read("transcript.json")
    edits = p.read("edits.json")
    cut = edl.word_cut_reasons(transcript, edits) if transcript and edits else {}
    return {
        "state": p.state,
        "transcript": transcript,
        "edits": edits,
        "cut": {str(k): v for k, v in cut.items()},
        "has_source": p.source.exists(),
    }


class OverridesReq(BaseModel):
    # word index -> True (force keep) / False (force cut) / None (clear override)
    overrides: dict[str, bool | None]


@app.patch("/api/projects/{pid}/overrides")
def patch_overrides(pid: str, req: OverridesReq):
    p = _get(pid)
    edits = p.read("edits.json")
    if edits is None:
        raise HTTPException(400, "process the project first")
    ov = edits.setdefault("overrides", {})
    for k, v in req.overrides.items():
        if v is None:
            ov.pop(k, None)
        else:
            ov[k] = v
    p.write("edits.json", edits)
    return get_project(pid)


class StyleReq(BaseModel):
    punch_in: bool | None = None
    captions: bool | None = None


@app.patch("/api/projects/{pid}/style")
def patch_style(pid: str, req: StyleReq):
    """Change render-only options (no AI re-run); takes effect on the next render."""
    p = _get(pid)
    edits = p.read("edits.json")
    if edits is None:
        raise HTTPException(400, "process the project first")
    edits.setdefault("opts", {}).update(req.model_dump(exclude_unset=True))
    p.write("edits.json", edits)
    return get_project(pid)


@app.post("/api/projects/{pid}/render")
def rerender(pid: str):
    p = _get(pid)
    if p.read("edits.json") is None:
        raise HTTPException(400, "process the project first")
    _start(p, run.rerender)
    return p.state


@app.post("/api/projects/{pid}/export/{name}")
def export(pid: str, name: str):
    p = _get(pid)
    if not any(o["name"] == name for o in p.state.get("outputs") or []):
        raise HTTPException(404, "no such output; render first")
    _start(p, run.export, name)
    return p.state


app.mount("/media", StaticFiles(directory=DATA_DIR), name="media")
