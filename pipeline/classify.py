"""Keyword-based topic tagging and ranking (works with no API key; refined by the LLM step if available)."""
from __future__ import annotations

import re

PRIMARY_W, SECONDARY_W = 3.0, 1.0
# Venue quality is NOT mixed into the score: papers are ordered by venue tier first (see run_weekly.rank),
# and this score only measures topic fit within a tier.


def compile_topics(topics: list[dict]) -> list[dict]:
    out = []
    for t in topics:
        pats = []  # (pattern, weak)
        for kw in t["keywords"]:
            weak = kw.startswith("~")
            kw = kw.lstrip("~")
            if kw.startswith("\\b"):
                pats.append((re.compile(kw, re.I), weak))
                continue
            esc = re.escape(kw).replace(r"\*", r"\w*")
            pats.append((re.compile(rf"(?<![\w-]){esc}(?![\w])", re.I), weak))
        out.append({**t, "patterns": pats})
    return out


def score_paper(p: dict, journal: dict, topics: list[dict]) -> None:
    text = f"{p['title']} . {p.get('abstract', '')}"
    strict = journal.get("strict", False) or p.get("type") == "preprint"
    ignore_weak = strict and journal.get("group") != "Preprints"  # econ preprints use econ vocabulary
    hits: dict[str, int] = {}
    for t in topics:
        n = sum(1 for pat, weak in t["patterns"] if not (weak and ignore_weak) and pat.search(text))
        if n:
            hits[t["id"]] = n
    for d in journal.get("default_topics", []) or []:
        hits[d] = max(hits.get(d, 0), 2)

    prio = {t["id"]: t["priority"] for t in topics}
    score = 0.0
    for tid, n in hits.items():
        w = PRIMARY_W if prio.get(tid) == "primary" else SECONDARY_W
        score += w * min(n, 3)
    has_primary = any(prio.get(t) == "primary" for t in hits)

    keep_all = journal.get("keep_all", journal.get("group") == "Top 5")
    if keep_all:
        keep = True
    elif strict:
        sec_hits = sum(n for t, n in hits.items() if prio.get(t) == "secondary")
        keep = has_primary or sec_hits >= 2
    else:
        keep = bool(hits)

    ordered = sorted(hits, key=lambda t: (prio.get(t) != "primary", -hits[t]))
    p["topics"] = ordered or ["other"]
    p["on_topic"] = bool(hits)
    p["score"] = round(score, 2)
    p["keep"] = keep


def first_sentences(text: str, max_chars: int = 260) -> str:
    if not text:
        return ""
    sents = re.split(r"(?<=[.!?])\s+", text)
    out = ""
    for s in sents:
        if len(out) + len(s) > max_chars and out:
            break
        out = f"{out} {s}".strip()
    return out if len(out) <= max_chars + 40 else out[:max_chars].rsplit(" ", 1)[0] + "…"
