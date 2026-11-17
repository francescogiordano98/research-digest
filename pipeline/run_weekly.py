"""Weekly pipeline: fetch -> tag/rank -> (AI triage + brief) -> write site data + RSS -> email.

Usage:
    python pipeline/run_weekly.py                # last 7 days, send email if SMTP configured
    python pipeline/run_weekly.py --days 14 --no-email
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

import yaml

sys.path.insert(0, str(Path(__file__).parent))
import classify  # noqa: E402
import emailer  # noqa: E402
import sources  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
DATA = SITE / "data"


def load_yaml(name: str) -> dict:
    return yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))


def fallback_brief(papers: list[dict], labels: dict[str, str], n_sources: int) -> str:
    lines = [f"This week the digest collected **{len(papers)} new papers** from {n_sources} outlets. "
             "Highest-ranked by topic match and venue:", ""]
    for p in papers[:10]:
        lines.append(f"- *{p['title']}* ({p['journal_short']}) — {labels.get(p['topics'][0], 'Other')}")
    return "\n".join(lines)


def write_rss(issues: list[dict], site_url: str) -> None:
    base = site_url.rstrip("/")
    items = []
    for iss in issues[:2]:
        for p in iss["papers"]:
            items.append(
                f"<item><title>{escape(p['title'])}</title><link>{escape(p['url'])}</link>"
                f"<guid isPermaLink=\"false\">{escape(p['id'])}</guid>"
                f"<category>{escape(p['journal'])}</category>"
                f"<description>{escape((p.get('summary') or '') + ' — ' + ', '.join(p['authors'][:6]))}</description>"
                f"<pubDate>{format_datetime(datetime.fromisoformat(iss['date']).replace(tzinfo=timezone.utc))}</pubDate></item>"
            )
    rss = (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        f"<title>Weekly Research Digest</title><link>{escape(base or '.')}</link>"
        "<description>New papers in economic theory, innovation, IO and AI from leading journals.</description>"
        + "".join(items) + "</channel></rss>"
    )
    (SITE / "feed.xml").write_text(rss, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--no-ai", action="store_true", help="skip Claude even if ANTHROPIC_API_KEY is set")
    ap.add_argument("--no-seen", action="store_true", help="test run: don't record papers as already shown")
    args = ap.parse_args()

    cfg, tcfg = load_yaml("sources.yaml"), load_yaml("topics.yaml")
    topics = classify.compile_topics(tcfg["topics"])
    labels = {t["id"]: t["label"] for t in tcfg["topics"]}
    today = date.today()
    since, until = today - timedelta(days=args.days), today
    print(f"Window {since} -> {until}")

    # 1. fetch
    journals = cfg["journals"]
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(lambda j: (j, sources.fetch_crossref_journal(j, since, until)), journals))
    # publisher RSS feeds look back further: some journals post issues before/after Crossref deposits;
    # the "seen" list below guarantees nothing is shown twice.
    rss_feeds = [j for j in journals if j.get("rss")] + cfg.get("working_papers", [])
    with ThreadPoolExecutor(max_workers=6) as ex:
        rss_results = list(ex.map(
            lambda j: (j, sources.fetch_rss(j, since - timedelta(days=0 if j.get("nber") else 45))), rss_feeds))

    seen_path = DATA / "seen.json"
    seen = set(json.loads(seen_path.read_text(encoding="utf-8"))) if seen_path.exists() else set()
    papers, jmeta = [], {}

    def add(j: dict, ps: list[dict], label: str) -> None:
        n = 0
        for p in ps:
            pid = p["id"].lower()
            if pid in jmeta or pid in seen:
                continue
            p["id"] = pid
            jmeta[pid] = j
            papers.append(p)
            n += 1
        print(f"  {label:>10}: {n}")

    for j, ps in results:
        add(j, ps, j["short"])
    for j, ps in rss_results:
        add({**j, "strict": j.get("strict", j.get("group") == "Preprints")}, ps, j["short"] + " rss")
    for feed in cfg.get("arxiv", []):
        add({"name": feed["name"], "group": feed.get("group", "Preprints"), "strict": True},
            sources.fetch_arxiv(feed, since), feed["category"])
    print(f"Fetched {len(papers)} new items; abstracts filled from OpenAlex: {sources.enrich_abstracts_openalex(papers)}")

    # 2. tag + keyword rank
    for p in papers:
        classify.score_paper(p, jmeta[p["id"]], topics)
    papers = [p for p in papers if p["keep"]]
    # Broad science outlets (Nature, Science, PNAS...): keep everything that hits a core topic,
    # but at most MAX_GENERAL papers that only match secondary topics (physics, biology, ...).
    prio = {t["id"]: t["priority"] for t in tcfg["topics"]}
    general = sorted((p for p in papers if p["group"] == "General Science"  # noqa: E501
                      and not any(prio.get(t) == "primary" for t in p["topics"])), key=lambda p: -p["score"])
    drop = {p["id"] for p in general[int(os.environ.get("MAX_GENERAL", 20)):]}
    papers = [p for p in papers if p["id"] not in drop]
    print(f"Kept {len(papers)} after topic filter")

    # 3. AI triage + summaries (optional)
    use_ai = bool(os.environ.get("ANTHROPIC_API_KEY")) and not args.no_ai
    if use_ai:
        import summarize
        summarize.triage(papers, list(labels))
    for p in papers:
        # Order: venue tier first (1 = top econ/management/top field, 2 = field-specific, 3 = other),
        # then on-topic before off-topic (e.g. a Top-5 macro paper), then topic fit within the band.
        # Fit = AI relevance (0-10) when available, else the keyword score (capped to the same scale).
        fit = p["relevance"] if "relevance" in p else min(p["score"], 20) / 2
        p["fit"] = round(fit, 2)
        p["rank"] = round((3 - p.get("tier", 3)) * 100 + (50 if p.get("on_topic", True) else 0) + fit, 2)
        if not p.get("summary"):
            p["summary"] = classify.first_sentences(p["abstract"])
    if use_ai:  # drop what the model judged irrelevant (but never drop Top-5 papers)
        papers = [p for p in papers if p.get("relevance", 5) >= 3 or p["group"] == "Top 5"]
    papers.sort(key=lambda p: -p["rank"])
    for k, p in enumerate(papers):
        p["pick"] = k < 10
        p["abstract"] = p["abstract"][:2000]
        p.pop("keep", None)
        p.pop("on_topic", None)

    n_sources = len({p["journal"] for p in papers})
    brief = ""
    if use_ai and papers:
        try:
            brief = summarize.weekly_brief(papers, labels)
        except Exception as e:
            print(f"  ! brief failed: {e}")
    brief = brief or fallback_brief(papers, labels, n_sources)

    # 4. write site data
    (DATA / "issues").mkdir(parents=True, exist_ok=True)
    idx_path = DATA / "index.json"
    index = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else {"issues": []}
    existing = {i["date"]: i for i in index["issues"]}
    number = existing[str(today)]["number"] if str(today) in existing else len(index["issues"]) + 1
    issue = {
        "number": number, "date": str(today), "window": f"{since} – {until}",
        "count": len(papers), "n_sources": n_sources, "ai": use_ai, "brief": brief,
        "topics": labels, "papers": papers,
    }
    (DATA / "issues" / f"{today}.json").write_text(json.dumps(issue, ensure_ascii=False, indent=1), encoding="utf-8")
    existing[str(today)] = {"number": number, "date": str(today), "count": len(papers)}
    index["issues"] = sorted(existing.values(), key=lambda i: i["date"], reverse=True)
    index["topics"] = labels
    idx_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    # remember every paper fetched this week (shown or filtered) so it never reappears in a later issue
    if not args.no_seen:
        seen |= set(jmeta)
        seen_path.write_text(json.dumps(sorted(seen)), encoding="utf-8")

    site_url = os.environ.get("SITE_URL", "")
    issues_full = [json.loads((DATA / "issues" / f"{i['date']}.json").read_text(encoding="utf-8")) for i in index["issues"][:2]]
    write_rss(issues_full, site_url)
    print(f"Wrote issue No. {number} ({len(papers)} papers)")

    # 5. email
    if not args.no_email:
        emailer.send(issue, site_url, labels, ROOT)
    # also save a preview of the email for inspection
    _, body = emailer.render_email(issue, site_url, labels)
    (ROOT / "last_email.html").write_text(body, encoding="utf-8")


if __name__ == "__main__":
    main()
