"""Pipeline orchestration. Each stage writes its own artifact, so it can be re-run on its own."""

import threading
import traceback

from ..project import Project
from . import clean, edl, narrate, render
from .ingest import convert_video, extract_audio, probe
from .transcribe import backend, transcribe


def _stage(p: Project, stage: str, progress: float | None) -> None:
    """progress=None means indeterminate (MLX transcription reports no progress)."""
    p.update_state(status="running", stage=stage, progress=None if progress is None else round(progress, 3), error=None)


def prepare(p: Project) -> dict:
    """Ingest and transcribe. Both are cached because they're the slow, deterministic stages.

    The audio is extracted first (seconds), so transcription runs while the video converts.
    """
    if p.raw is None:
        raise RuntimeError("This project has no uploaded video.")
    info = p.state.get("media")
    if not info or "fps" not in info:  # projects from before frame-rate detection
        info = probe(p.raw)
    if not info["has_audio"]:
        raise RuntimeError("This video has no audio track. Enable the microphone when recording.")
    p.update_state(media=info)

    if not p.audio.exists():
        _stage(p, "ingest", 0.0)
        extract_audio(p.raw, p.audio)

    video_error: list[BaseException] = []
    video = None
    if not p.source.exists():
        p.update_state(video_progress=0.0)

        def convert():
            try:
                convert_video(p.raw, p.source, info, lambda f: p.update_state(video_progress=round(f, 3)))
                p.update_state(video_progress=1.0)
            except BaseException as e:  # re-raised on the main pipeline thread below
                video_error.append(e)

        video = threading.Thread(target=convert, daemon=True)
        video.start()

    transcript = p.read("transcript.json")
    if transcript is None:
        mlx = backend() == "mlx"
        _stage(p, "transcribe", None if mlx else 0.05)
        transcript = transcribe(p.audio, info["duration"], None if mlx else lambda f: _stage(p, "transcribe", 0.05 + 0.6 * f))
        p.write("transcript.json", transcript)

    if video:
        if video.is_alive():
            _stage(p, "ingest", None)
        video.join()
        if video_error:
            raise video_error[0]
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
    for old in p.outputs.glob("clip_*.mp4" if clips else "main*.mp4"):  # previews and their exports
        old.unlink()
    outputs = [
        o for o in p.state.get("outputs") or []
        if o["name"].startswith("clip_") != clips and (p.dir / o["file"]).exists()
    ]
    for n, (name, ranges) in enumerate(plan.items()):
        _stage(p, "render", 0.75 + 0.25 * n / max(1, len(plan)))
        if not ranges:
            continue
        out = render.render(p.source, ranges, p.outputs / f"{name}.mp4", vertical=vertical and name != "main",
                            face_track=edits.get("opts", {}).get("reframe", "face") == "face")
        outputs.append({"name": name, "file": f"outputs/{out.name}", "duration": edl.total(ranges), "cuts": len(ranges)})
    return outputs


def export_output(p: Project, name: str) -> list[dict]:
    """Render one output at full source resolution, from the same cuts as its preview."""
    plan = p.read("edl.json") or {}
    outputs = p.state.get("outputs") or []
    entry = next((o for o in outputs if o["name"] == name), None)
    if entry is None or not plan.get(name):
        raise RuntimeError(f"No rendered output named {name}. Render first.")
    _stage(p, "export", None)
    vertical = bool((p.read("edits.json") or {}).get("opts", {}).get("vertical")) and name != "main"
    out = render.render(p.source, plan[name], p.outputs / f"{name}.full.mp4", vertical=vertical, max_height=None,
                        face_track=(p.read("edits.json") or {}).get("opts", {}).get("reframe", "face") == "face")
    entry["export_file"] = f"outputs/{out.name}"
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


def export(p: Project, name: str) -> None:
    _guard(p, lambda: export_output(p, name))
