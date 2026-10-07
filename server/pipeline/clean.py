"""Rule-based cleanup with no LLM: filler words, stutters, and cut-off words."""

import re

FILLERS = {"um", "umm", "uh", "uhh", "uhm", "er", "erm", "ah", "ahh", "hmm", "hm", "mm", "mhm"}

_strip = re.compile(r"[^\w'-]+")


def norm(word: str) -> str:
    return _strip.sub("", word.lower()).strip("'")


def auto_cuts(words: list[dict]) -> dict[str, str]:
    """Return {word_index: reason} for words that should be removed regardless of context.

    Context-dependent fillers ("like", "you know", "so") are left to the LLM pass.
    """
    cuts: dict[str, str] = {}
    for i, w in enumerate(words):
        n = norm(w["w"])
        if n in FILLERS:
            cuts[str(i)] = "filler"
            continue
        # Cut-off word: "wh-" / "th-"
        if n.endswith("-") and len(n) <= 6:
            cuts[str(i)] = "stutter"
            continue
        # Stutter: "I I think", "the the". Cut the earlier copy.
        nxt = words[i + 1] if i + 1 < len(words) else None
        if nxt and n and n == norm(nxt["w"]) and nxt["start"] - w["end"] < 0.6:
            cuts[str(i)] = "stutter"
    return cuts
