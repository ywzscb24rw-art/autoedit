"""HTTP API for the web app.  Run: .venv/bin/uvicorn server.main:app --reload --port 8000"""

import threading
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .pipeline import edl, run
from .project import DATA_DIR, Project

app = FastAPI(title="AutoEdit")


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
    with open(p.dir / f"raw.{ext}", "wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)
    return p.state


class ProcessReq(BaseModel):
    mode: Literal["clean", "clips"] = "clean"
    use_ai: bool = True
    vertical: bool = False
    min_s: float = 30
    max_s: float = 90
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


@app.post("/api/projects/{pid}/render")
def rerender(pid: str):
    p = _get(pid)
    if p.read("edits.json") is None:
        raise HTTPException(400, "process the project first")
    _start(p, run.rerender)
    return p.state


app.mount("/media", StaticFiles(directory=DATA_DIR), name="media")
