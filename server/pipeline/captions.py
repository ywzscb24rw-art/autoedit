"""Burned-in, word-by-word captions in the short-form style: 1-3 words at a time, bold, with
the word being spoken highlighted.

This ffmpeg build has no text filters (no libass or drawtext), so captions are drawn with
Pillow as transparent PNG strips and overlaid. Each piece of the edit gets its own caption
track, built as an ffmpeg concat list of images with exact durations.
"""

import re
from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONTS = [
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
WHITE, HIGHLIGHT, OUTLINE = (255, 255, 255, 255), (255, 225, 77, 255), (0, 0, 0, 255)

MAX_WORDS, MAX_CHARS = 3, 18
PAGE_BREAK_GAP = 0.4  # a pause this long starts a new caption
SENTENCE_GAP = 0.12  # a capitalised word after a pause this long starts one too (Whisper often
                     # drops the period between sentences but keeps the capital)
HOLD_GAP = 0.6  # a caption stays up through pauses shorter than this
LINGER = 0.3  # and lingers this long after its last word otherwise

_clean = re.compile(r"[^\w'%$&@#-]+")


def tidy(word: str) -> str:
    return _clean.sub("", word).upper()


def _sentence_start(word: str) -> bool:
    w = word.strip()
    return bool(w) and w[0].isupper() and w.rstrip(".,!?").split("'")[0] != "I"


def pages(words: list[dict]) -> list[dict]:
    """Group timed words [{w, start, end}] into caption pages [{start, end, words}]."""
    out: list[dict] = []
    cur: list[dict] = []
    for w in words:
        text = tidy(w["w"])
        if not text:
            continue
        item = {"text": text, "start": w["start"], "end": w["end"]}
        gap = w["start"] - cur[-1]["end"] if cur else 0.0
        if cur and (
            len(cur) >= MAX_WORDS
            or sum(len(x["text"]) + 1 for x in cur) + len(text) > MAX_CHARS
            or gap > PAGE_BREAK_GAP
            or (_sentence_start(w["w"]) and gap > SENTENCE_GAP)
        ):
            out.append({"words": cur})
            cur = []
        cur.append(item)
        if re.search(r"[.?!,;:]$", w["w"].strip()):  # end the caption at punctuation
            out.append({"words": cur})
            cur = []
    if cur:
        out.append({"words": cur})
    for i, pg in enumerate(out):
        pg["start"] = pg["words"][0]["start"]
        last_end = pg["words"][-1]["end"]
        nxt = out[i + 1]["words"][0]["start"] if i + 1 < len(out) else None
        pg["end"] = nxt if nxt is not None and nxt - last_end < HOLD_GAP else last_end + LINGER
    return out


@cache
def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONTS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def layout(out_w: int, out_h: int) -> tuple[int, int, int]:
    """(font size, strip height, strip top) for an output frame."""
    vertical = out_h > out_w
    size = round(out_h * (0.042 if vertical else 0.062))
    strip_h = round(size * 3.2)  # room for two lines plus the outline
    centre_y = out_h * (0.70 if vertical else 0.83)
    return size, strip_h, int(centre_y - strip_h / 2)


def draw(texts: list[str], active: int | None, out_w: int, out_h: int) -> Image.Image:
    """One transparent caption strip, with texts[active] highlighted."""
    size, strip_h, _ = layout(out_w, out_h)
    font, stroke = _font(size), max(2, round(size * 0.12))
    img = Image.new("RGBA", (out_w, strip_h), (0, 0, 0, 0))
    if not texts:
        return img
    d = ImageDraw.Draw(img)
    space = d.textlength(" ", font=font)
    widths = [d.textlength(t, font=font) for t in texts]
    # Wrap onto a second line if the words don't fit in 88% of the width.
    lines, line, line_w = [], [], 0.0
    for i, w in enumerate(widths):
        if line and line_w + space + w > out_w * 0.88:
            lines.append(line)
            line, line_w = [], 0.0
        line_w += (space if line else 0) + w
        line.append(i)
    lines.append(line)
    line_h = size * 1.2
    y = (strip_h - line_h * len(lines)) / 2
    for ln in lines:
        x = (out_w - (sum(widths[i] for i in ln) + space * (len(ln) - 1))) / 2
        for i in ln:
            fill = HIGHLIGHT if i == active else WHITE
            d.text((x, y), texts[i], font=font, fill=fill, stroke_width=stroke, stroke_fill=OUTLINE)
            x += widths[i] + space
        y += line_h
    return img


def track(words: list[dict], start: float, end: float, out_w: int, out_h: int, tmp: Path) -> Path | None:
    """Caption track for one piece of the edit (source times [start, end]): a concat list of
    PNG strips with durations, timed relative to the piece. None if nothing is said in it."""
    inside = [w for w in words if start <= (w["start"] + w["end"]) / 2 < end]
    if not inside:
        return None
    # Each interval shows one page with one word highlighted; gaps show an empty strip.
    events: list[tuple[float, float, tuple[str, ...], int | None]] = []
    for pg in pages(inside):
        texts = tuple(w["text"] for w in pg["words"])
        for i, w in enumerate(pg["words"]):
            a = pg["start"] if i == 0 else w["start"]
            b = pg["words"][i + 1]["start"] if i + 1 < len(pg["words"]) else pg["end"]
            events.append((a, b, texts, i))
    images: dict[tuple, Path] = {}
    entries: list[tuple[Path, float]] = []
    t = start

    def image(texts: tuple[str, ...], active: int | None) -> Path:
        key = (texts, active)
        if key not in images:
            f = tmp / f"cap{len(images):05d}.png"
            draw(list(texts), active, out_w, out_h).save(f)
            images[key] = f
        return images[key]

    for a, b, texts, active in events:
        a, b = max(a, t), min(b, end)
        if b <= a:
            continue
        if a > t:
            entries.append((image((), None), a - t))
        entries.append((image(texts, active), b - a))
        t = b
    if t < end:
        entries.append((image((), None), end - t))
    listing = tmp / "captions.txt"
    body = "".join(f"file '{f}'\nduration {d:.6f}\n" for f, d in entries)
    # The concat demuxer ignores the last entry's duration unless the file is listed again.
    listing.write_text(body + f"file '{entries[-1][0]}'\n")
    return listing
