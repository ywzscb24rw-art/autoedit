"""Pipeline orchestration. Each stage writes its own artifact, so it can be re-run on its own."""

import traceback

from ..project import Project
from . import clean, edl, narrate, render
from .ingest import ingest
from .transcribe import transcribe


def _stage(p: Project, stage: str, progress: float) -> None:
    p.update_state(status="running", stage=stage, progress=round(progress, 3), error=None)


def prepare(p: Project) -> dict:
    """Ingest and transcribe. These are cached because they're the slow, deterministic stages."""
    if not p.source.exists():
        _stage(p, "ingest", 0.0)
        info = ingest(p.raw, p.source, p.audio)
        p.update_state(media=info)
    transcript = p.read("transcript.json")
    if transcript is None:
        _stage(p, "transcribe", 0.05)
        duration = p.state["media"]["duration"]
        transcript = transcribe(p.audio, duration, lambda f: _stage(p, "transcribe", 0.05 + 0.6 * f))
        p.write("transcript.json", transcript)
    return transcript


def edit(p: Project, transcript: dict, mode: str, use_ai: bool, opts: dict) -> dict:
    """Build edits.json from rule-based cuts plus, optionally, Claude's decisions."""
    _stage(p, "ai-edit" if use_ai else "clean", 0.7)
    auto = clean.auto_cuts(transcript["words"])
    prev = p.read("edits.json", {})
    edits = {
        "mode": mode,
        "use_ai": use_ai,
        "opts": opts,
        "auto_cuts": auto,
        "segment_cuts": {},
        "order": [s["id"] for s in transcript["segments"]],
        "clips": [],
        "summary": "",
        # Manual word toggles survive a re-run of the AI edit.
        "overrides": prev.get("overrides", {}),
    }
    if use_ai:
        if mode == "clips":
            edits.update(narrate.find_clips(
                transcript, auto,
                min_s=opts.get("min_s", 30), max_s=opts.get("max_s", 90), max_clips=opts.get("max_clips", 5),
            ))
        else:
            edits.update(narrate.clean_edit(transcript, auto))
    elif mode == "clips":
        raise RuntimeError("Clips mode needs the AI edit (it chooses what to clip).")
    p.write("edits.json", edits)
    return edits


def render_outputs(p: Project, transcript: dict, edits: dict) -> list[dict]:
    plan = edl.compute(transcript, edits)
    p.write("edl.json", plan)
    vertical = bool(edits.get("opts", {}).get("vertical"))
    # Only replace this mode's outputs, so a clean edit and its clips can coexist.
    clips = edits.get("mode") == "clips"
    for old in p.outputs.glob("clip_*.mp4" if clips else "main.mp4"):
        old.unlink()
    outputs = [
        o for o in p.state.get("outputs") or []
        if o["name"].startswith("clip_") != clips and (p.dir / o["file"]).exists()
    ]
    for n, (name, ranges) in enumerate(plan.items()):
        _stage(p, "render", 0.75 + 0.25 * n / max(1, len(plan)))
        if not ranges:
            continue
        out = render.render(p.source, ranges, p.outputs / f"{name}.mp4", vertical=vertical and name != "main")
        outputs.append({"name": name, "file": f"outputs/{out.name}", "duration": edl.total(ranges), "cuts": len(ranges)})
    return outputs


def _guard(p: Project, fn) -> None:
    try:
        outputs = fn()
        p.update_state(status="done", stage=None, progress=1.0, outputs=outputs)
    except Exception as e:  # surface every failure in the UI instead of dying silently in a thread
        traceback.print_exc()
        p.update_state(status="error", error=f"{type(e).__name__}: {e}")


def process(p: Project, mode: str = "clean", use_ai: bool = True, opts: dict | None = None) -> None:
    def go():
        t = prepare(p)
        e = edit(p, t, mode, use_ai, opts or {})
        return render_outputs(p, t, e)

    _guard(p, go)


def rerender(p: Project) -> None:
    _guard(p, lambda: render_outputs(p, p.read("transcript.json"), p.read("edits.json")))
