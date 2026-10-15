"""A transparent 0-100 heuristic for sorting names. Not an appraisal."""
from __future__ import annotations

from functools import lru_cache
from importlib import resources

VOWELS = set("aeiouy")
LENGTH_POINTS = {1: 40, 2: 35, 3: 28, 4: 20, 5: 12, 6: 6, 7: 2, 8: 0}


@lru_cache(maxsize=1)
def hot_words() -> frozenset[str]:
    text = resources.files("hushname").joinpath("data/hot_words.txt").read_text()
    return frozenset(
        w for w in (line.split("#", 1)[0].strip().lower() for line in text.splitlines())
        if len(w) >= 2
    )


def score(label: str, tld_weight: int = 0) -> int:
    s = 50
    n = len(label)
    s += LENGTH_POINTS.get(n, -2 * (n - 8))
    if "-" in label:
        s -= 25
    if any(c.isdigit() for c in label):
        s -= 15
    # Pronounceability: long consonant clusters are hard to say and spell.
    run = longest = 0
    for c in label:
        run = 0 if c in VOWELS else run + 1
        longest = max(longest, run)
    if longest >= 4:
        s -= 15
    elif longest == 3:
        s -= 5
    words = hot_words()
    if label in words or any(len(w) >= 3 and w in label for w in words):
        s += 8
    s += tld_weight
    return max(0, min(100, s))
