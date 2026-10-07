"""Local Whisper transcription with word-level timestamps, regrouped into sentence segments."""

import os
import re
import wave
from pathlib import Path
from typing import Callable

# Whisper normally "cleans up" speech and silently drops fillers. Priming it with a
# disfluent prompt makes it transcribe them, which we need in order to cut them.
FILLER_PROMPT = "Umm, so, uh, let me think, like, hmm... Okay. Uh, I mean, um, you know, yeah."

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        _model = WhisperModel(os.environ.get("WHISPER_MODEL", "small.en"), device="cpu", compute_type="int8")
    return _model


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
    model = _get_model()
    segs, info = model.transcribe(
        load_wav(audio),
        word_timestamps=True,
        vad_filter=False,  # VAD would hide the silences we want to measure
        initial_prompt=FILLER_PROMPT,
        condition_on_previous_text=False,  # avoids repetition loops and keeps fillers coming
    )
    words = []
    for seg in segs:
        for w in seg.words or []:
            if not w.word.strip():
                continue
            words.append({
                "i": len(words),
                "w": w.word,  # keeps Whisper's leading space so joins read naturally
                "start": round(w.start, 3),
                "end": round(max(w.end, w.start + 0.02), 3),
                "prob": round(w.probability, 3),
            })
        if on_progress and duration:
            on_progress(min(seg.end / duration, 1.0))
    return {
        "language": info.language,
        "duration": duration,
        "words": words,
        "segments": group_segments(words),
    }
