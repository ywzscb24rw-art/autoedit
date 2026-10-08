"""Local Whisper transcription with word-level timestamps, regrouped into sentence segments."""

import os
import platform
import re
import wave
from pathlib import Path
from typing import Callable

# Whisper normally "cleans up" speech and silently drops fillers. Priming it with a
# disfluent prompt makes it transcribe them, which we need in order to cut them.
FILLER_PROMPT = "Umm, so, uh, let me think, like, hmm... Okay. Uh, I mean, um, you know, yeah."

MODEL = os.environ.get("WHISPER_MODEL", "large-v3-turbo")

# On Apple Silicon, MLX runs Whisper on the GPU: about 2x faster than faster-whisper on the CPU,
# fast enough that large-v3-turbo costs the same time as small.en does on the CPU.
MLX_REPOS = {
    "tiny.en": "mlx-community/whisper-tiny.en-mlx",
    "base.en": "mlx-community/whisper-base.en-mlx",
    "small.en": "mlx-community/whisper-small.en-mlx",
    "medium.en": "mlx-community/whisper-medium.en-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}


def backend() -> str:
    choice = os.environ.get("WHISPER_BACKEND", "auto")
    if choice != "auto":
        return choice
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        try:
            import mlx_whisper  # noqa: F401

            return "mlx"
        except ImportError:
            pass
    return "cpu"


_cpu_model = None


def _cpu_transcribe(audio, on_progress, duration):
    global _cpu_model
    if _cpu_model is None:
        from faster_whisper import WhisperModel

        _cpu_model = WhisperModel(MODEL, device="cpu", compute_type="int8")
    segs, info = _cpu_model.transcribe(
        audio,
        word_timestamps=True,
        vad_filter=False,  # VAD would hide the silences we want to measure
        initial_prompt=FILLER_PROMPT,
        condition_on_previous_text=False,  # avoids repetition loops and keeps fillers coming
    )
    for seg in segs:
        if on_progress and duration:
            on_progress(min(seg.end / duration, 1.0))
        for w in seg.words or []:
            yield w.word, w.start, w.end, w.probability


def _mlx_transcribe(audio):
    import mlx_whisper

    result = mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=MLX_REPOS.get(MODEL, MODEL),
        word_timestamps=True,
        initial_prompt=FILLER_PROMPT,
        condition_on_previous_text=False,
    )
    for seg in result["segments"]:
        for w in seg.get("words", []):
            yield w["word"], w["start"], w["end"], w["probability"]


SENTENCE_END = re.compile(r"[.?!]['\")\]]*$")


def group_segments(words: list[dict], pause_break: float = 1.2, max_words: int = 45) -> list[dict]:
    """Split the word stream into sentence-like segments: the unit the LLM keeps or cuts."""
    segments: list[dict] = []
    start = 0
    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else None
        boundary = (
            nxt is None
            or SENTENCE_END.search(w["w"].strip())
            or nxt["start"] - w["end"] > pause_break
            or i - start + 1 >= max_words
        )
        if boundary:
            seg_words = words[start : i + 1]
            sid = len(segments)
            for sw in seg_words:
                sw["seg"] = sid
            segments.append({
                "id": sid,
                "start": seg_words[0]["start"],
                "end": seg_words[-1]["end"],
                "words": [start, i + 1],  # half-open index range into words
                "text": "".join(sw["w"] for sw in seg_words).strip(),
            })
            start = i + 1
    return segments


def load_wav(path: Path):
    """Read the 16 kHz mono PCM WAV that ingest wrote. Avoids faster-whisper's PyAV decoder."""
    import numpy as np

    with wave.open(str(path), "rb") as f:
        pcm = f.readframes(f.getnframes())
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe(audio: Path, duration: float, on_progress: Callable[[float], None] | None = None) -> dict:
    """on_progress is only called by the CPU backend; MLX gives no progress callback."""
    samples = load_wav(audio)
    raw = _mlx_transcribe(samples) if backend() == "mlx" else _cpu_transcribe(samples, on_progress, duration)
    words = []
    for text, start, end, prob in raw:
        if not text.strip():
            continue
        words.append({
            "i": len(words),
            "w": text,  # keeps Whisper's leading space so joins read naturally
            "start": round(start, 3),
            "end": round(max(end, start + 0.02), 3),
            "prob": round(prob, 3),
        })
    return {
        "language": "en",
        "duration": duration,
        "model": f"{backend()}:{MODEL}",
        "words": words,
        "segments": group_segments(words),
    }
