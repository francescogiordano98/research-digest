"""Optional AI layer (Claude): relevance triage, one-line summaries and the weekly editorial brief.

Runs only if ANTHROPIC_API_KEY is set; otherwise the pipeline falls back to keyword ranking and abstract excerpts.
"""
from __future__ import annotations

import json
import os

MODEL = os.environ.get("DIGEST_MODEL", "claude-opus-5-5")

READER_PROFILE = """The reader is a PhD-level economist. Core interests (highest priority):
1. Game theory and economic theory broadly: information economics, Bayesian persuasion, mechanism design, learning,
   model misspecification, decision theory, repeated/dynamic games, contracts, matching, social choice.
2. Innovation and entrepreneurship: R&D, patents, science, startups, venture capital, technology diffusion.
3. Industrial organization and the economic impact of AI: competition, platforms, pricing, antitrust, automation, LLMs.
Secondary interests: economic history, decision theory/psychology of choice, AI/technology/energy/robotics/computation,
and notable physics or biology results of broad significance."""


def _client():
    import anthropic
    return anthropic.Anthropic()


def _call(system: str, user: str, schema: dict | None, effort: str, max_tokens: int = 16000) -> str:
    """One Messages call; tries server-side refusal fallbacks, retries without them if unsupported."""
    import anthropic
    client = _client()
    kwargs = dict(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort, **({"format": {"type": "json_schema", "schema": schema}} if schema else {})},
    )
    try:
        resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    except anthropic.BadRequestError:
        resp = client.messages.create(**kwargs)
    if resp.stop_reason == "refusal":
        raise RuntimeError("model declined the request")
    return next(b.text for b in resp.content if b.type == "text")


TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "papers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "relevance": {"type": "integer"},
                    "topics": {"type": "array", "items": {"type": "string"}},
                    "summary": {"type": "string"},
                },
                "required": ["id", "relevance", "topics", "summary"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["papers"],
    "additionalProperties": False,
}


def triage(papers: list[dict], topic_ids: list[str], batch: int = 30) -> None:
    system = (
        READER_PROFILE
        + "\n\nFor each paper: rate relevance to this reader from 0 (irrelevant) to 10 (must read); "
        f"assign 1-3 topic ids from {topic_ids + ['other']}; and write ONE plain sentence (max 35 words) "
        "stating the paper's main result or contribution - concrete, no hype, no 'this paper'. "
        "If there is no abstract, infer cautiously from the title and say less rather than invent results."
    )
    for i in range(0, len(papers), batch):
        chunk = papers[i:i + batch]
        payload = [
            {"id": p["id"], "venue": p["journal"], "title": p["title"], "abstract": p["abstract"][:2500]}
            for p in chunk
        ]
        try:
            out = json.loads(_call(system, json.dumps(payload, ensure_ascii=False), TRIAGE_SCHEMA, effort="low"))
        except Exception as e:  # keep the pipeline alive if the API hiccups
            print(f"  ! triage batch {i // batch} failed: {e}")
            continue
        by_id = {r["id"]: r for r in out["papers"]}
        for p in chunk:
            r = by_id.get(p["id"])
            if not r:
                continue
            p["relevance"] = max(0, min(10, int(r["relevance"])))
            p["summary"] = r["summary"].strip()
            valid = [t for t in r["topics"] if t in topic_ids]
            if valid:
                p["topics"] = valid
        print(f"  triaged {min(i + batch, len(papers))}/{len(papers)}")


def weekly_brief(papers: list[dict], topic_labels: dict[str, str]) -> str:
    top = papers[:45]
    payload = [
        {"n": k + 1, "venue": p["journal_short"], "title": p["title"],
         "topics": [topic_labels.get(t, t) for t in p["topics"]], "summary": p.get("summary", "")}
        for k, p in enumerate(top)
    ]
    system = (
        READER_PROFILE
        + "\n\nWrite this week's editorial brief for the digest, in Markdown. Structure: a 2-3 sentence overview of the "
        "week; then short sections (### headings) for the main themes, each 2-4 sentences naming the most important "
        "papers (title in *italics*, venue in parentheses) and why they matter, connecting papers where natural. "
        "Total 250-400 words. Be precise and sober; never invent results beyond the summaries given."
    )
    return _call(system, json.dumps(payload, ensure_ascii=False), None, effort="medium").strip()
