"""Pipeline orchestration. Each stage writes its own artifact, so it can be re-run on its own."""

import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

from ..project import Project
from . import broll, burnin, clean, edl, narrate, reframe, render
from .ingest import convert_video, extract_audio, make_proxy, needs_proxy, probe
from .transcribe import backend, transcribe


# Default clip length (seconds) per content type. Vlog moments are short and punchy.
CLIP_LENGTHS = {"screen": (30, 90), "talking": (30, 90), "vlog": (15, 60)}
# Style defaults per content type; opts can override each one.
STYLE = {
    "screen": {"punch_in": False, "captions": False},
    "talking": {"punch_in": True, "captions": True},
    "vlog": {"punch_in": False, "captions": False},  # vlogs usually carry their own subtitles
}


def render_style(edits: dict, name: str, burned_captions: bool = False) -> dict:
    """render() keyword options for one output. Captions default off when the footage already
    has its own burned in; an explicit choice in opts always wins."""
    opts = edits.get("opts", {})
    defaults = dict(STYLE.get(opts.get("content", "screen"), STYLE["screen"]))
    if burned_captions:
        defaults["captions"] = False
    style = {**defaults, **{k: opts[k] for k in ("punch_in", "captions") if opts.get(k) is not None}}
    vertical = bool(opts.get("vertical")) and name != "main"
    return {
        "vertical": vertical,
        "face_track": opts.get("reframe", "face") == "face",
        "punch_in": style["punch_in"],
        "captions": style["captions"],
    }


def _stage(p: Project, stage: str, progress: float | None) -> None:
    """progress=None means indeterminate (MLX transcription reports no progress)."""
    p.update_state(status="running", stage=stage, progress=None if progress is None else round(progress, 3), error=None)


def prepare(p: Project) -> dict:
    """Ingest and transcribe. Both are cached because they're the slow, deterministic stages.

    The audio is extracted first (seconds), so transcription runs while the video converts
    and, for 4K or sparse-keyframe footage, a 1080p preview proxy is made.
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

    def progress(f: float) -> None:
        p.update_state(video_progress=round(f, 3))

    def convert():
        try:
            if not p.source.exists():
                convert_video(p.raw, p.source, info, progress)
            if not p.proxy.exists() and needs_proxy(p.source):
                p.update_state(video_progress=0.0)
                make_proxy(p.source, p.proxy, progress)
            p.update_state(video_progress=1.0)
        except BaseException as e:  # re-raised on the main pipeline thread below
            video_error.append(e)

    if not p.source.exists() or (not p.proxy.exists() and needs_proxy(p.source)):
        p.update_state(video_progress=0.0)
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
    detect_burned_captions(p, transcript)
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
            content = opts.get("content", "screen")
            lo, hi = CLIP_LENGTHS.get(content, CLIP_LENGTHS["screen"])
            edits.update(narrate.find_clips(
                transcript, auto, content=content,
                min_s=opts.get("min_s") or lo, max_s=opts.get("max_s") or hi, max_clips=opts.get("max_clips", 5),
            ))
        else:
            edits.update(narrate.clean_edit(transcript, auto, content=opts.get("content", "screen")))
    elif mode == "clips":
        raise RuntimeError("Clips mode needs the AI edit (it chooses what to clip).")
    p.write("edits.json", edits)
    return edits


def detect_burned_captions(p: Project, transcript: dict) -> bool:
    """Cached in state: does the footage already carry its own captions?"""
    if p.state.get("burned_captions") is None:
        p.update_state(burned_captions=burnin.has_burned_captions(p.preview_source, transcript))
    return p.state["burned_captions"]


def _render_kwargs(p: Project, transcript: dict, edits: dict, name: str) -> dict:
    style = render_style(edits, name, detect_burned_captions(p, transcript))
    words = edl.kept_words(transcript, edits).get(name) if style.pop("captions") else None
    return {**style, "caption_words": words}


def scene_cuts_for(p: Project, transcript: dict, edits: dict) -> list[float]:
    """Scene cuts inside the stretches of footage that B-roll may keep (cached per stretch)."""
    wins = edl.windows(transcript, edits)
    if not wins:
        return []
    cache = p.read("cuts.json", {})
    todo = [w for w in wins if f"{w[0]:.3f}-{w[1]:.3f}" not in cache]
    if todo:
        _stage(p, "scenes", None)
        with ThreadPoolExecutor(max_workers=4) as pool:
            found = list(pool.map(lambda w: reframe.scene_cuts(p.preview_source, w[0], w[1] - w[0]), todo))
        for w, cuts in zip(todo, found):
            cache[f"{w[0]:.3f}-{w[1]:.3f}"] = cuts
        p.write("cuts.json", cache)
    return sorted({c for w in wins for c in cache[f"{w[0]:.3f}-{w[1]:.3f}"]})


def broll_filter(p: Project, transcript: dict, edits: dict, cuts: list[float]):
    """For vlogs with the AI edit on, Claude looks at each proposed B-roll shot and keeps or
    skips it. Returns allow(start, end) for the EDL, or None to keep every proposed shot."""
    if not edits.get("use_ai") or not edl.params_for(edits).broll_gap:
        return None
    pieces = edl.broll_pieces(transcript, edits, cuts)
    if not pieces:
        return None
    cache = p.read("broll.json", {})
    if any(broll.key(pc) not in cache for pc in pieces):
        _stage(p, "broll", None)
        cache = broll.review(p.preview_source, transcript, edits, pieces, cache)
        p.write("broll.json", cache)
    return lambda s, e: cache.get(f"{s:.3f}-{e:.3f}", {}).get("keep", True)


def ensure_proxy(p: Project) -> None:
    """Projects from before proxies existed get one on their next render."""
    if not p.proxy.exists() and needs_proxy(p.source):
        _stage(p, "proxy", 0.0)
        make_proxy(p.source, p.proxy, lambda f: _stage(p, "proxy", f))


def render_outputs(p: Project, transcript: dict, edits: dict) -> list[dict]:
    ensure_proxy(p)
    cuts = scene_cuts_for(p, transcript, edits)
    plan = edl.compute(transcript, edits, cuts=cuts, allow=broll_filter(p, transcript, edits, cuts))
    p.write("edl.json", plan)
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
        kw = _render_kwargs(p, transcript, edits, name)
        # 16:9 previews render from the 1080p proxy. A 9:16 crop needs the full-resolution
        # source to stay sharp, but its faces are still found on the proxy.
        src = p.source if kw["vertical"] else p.preview_source
        out = render.render(src, ranges, p.outputs / f"{name}.mp4", analysis_source=p.preview_source, **kw)
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
    out = render.render(p.source, plan[name], p.outputs / f"{name}.full.mp4", max_height=None,
                        analysis_source=p.preview_source,
                        **_render_kwargs(p, p.read("transcript.json"), p.read("edits.json") or {}, name))
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
