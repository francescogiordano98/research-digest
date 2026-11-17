"""Fetch new articles from Crossref (journals), OpenAlex (abstract enrichment) and arXiv (preprints)."""
from __future__ import annotations

import html
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime

import requests

UA = "research-digest/1.0 (weekly literature digest; non-commercial)"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = UA

# Optional: Crossref/OpenAlex "polite pool" contact. Set CONTACT_EMAIL in the environment if you want it.
CONTACT = os.environ.get("CONTACT_EMAIL", "").strip()

SKIP_TITLES = re.compile(
    r"^(front matter|back matter|issue information|editorial board|erratum|errata|corrigendum|correction|"
    r"retraction|announcement|report of the|index|masthead|cover|table of contents|in this issue|"
    r"acknowledg|reviewers|referees|editor'?s? (note|introduction)|recent referees|forthcoming|"
    r"minutes of|front cover|back cover|call for papers|obituary|frontmatter|backmatter)",
    re.I,
)


def _get(url: str, params: dict | None = None, tries: int = 3) -> requests.Response | None:
    for attempt in range(tries):
        try:
            r = SESSION.get(url, params=params, timeout=40)
            if r.status_code == 404:
                return None
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if attempt == tries - 1:
                print(f"  ! request failed: {url} ({e})")
            time.sleep(2 * (attempt + 1))
    return None


def clean_text(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)  # strip JATS / HTML tags
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^(abstract|summary)[:.\s]+", "", s, flags=re.I)
    return s


def _authors(item: dict) -> list[str]:
    out = []
    for a in item.get("author", []) or []:
        name = " ".join(x for x in [a.get("given"), a.get("family")] if x) or a.get("name")
        if name:
            out.append(name)
    return out


def _date_parts(item: dict, key: str) -> str | None:
    parts = (item.get(key) or {}).get("date-parts") or []
    if parts and parts[0] and parts[0][0]:
        p = parts[0] + [1, 1]
        return f"{p[0]:04d}-{p[1]:02d}-{p[2]:02d}"
    return None


def fetch_crossref_journal(journal: dict, since: date, until: date) -> list[dict]:
    seen, papers = set(), []
    for issn in journal["issn"]:
        params = {
            "filter": f"from-created-date:{since},until-created-date:{until},type:journal-article",
            "rows": 300,
            "select": "DOI,title,author,abstract,created,published,published-online,URL,container-title",
        }
        if CONTACT:
            params["mailto"] = CONTACT
        r = _get(f"https://api.crossref.org/journals/{issn}/works", params)
        if r is None:
            continue
        for it in r.json().get("message", {}).get("items", []):
            doi = (it.get("DOI") or "").lower()
            title = clean_text((it.get("title") or [""])[0])
            if not doi or doi in seen or not title or SKIP_TITLES.match(title):
                continue
            seen.add(doi)
            papers.append({
                "id": doi,
                "doi": doi,
                "title": title,
                "authors": _authors(it),
                "abstract": clean_text(it.get("abstract")),
                "url": f"https://doi.org/{doi}",
                "date": _date_parts(it, "published-online") or _date_parts(it, "created"),
                "journal": journal["name"],
                "journal_short": journal["short"],
                "group": journal["group"],
                "tier": journal.get("tier", 2),
                "type": "article",
            })
        time.sleep(0.3)
    return papers


def enrich_abstracts_openalex(papers: list[dict]) -> int:
    """Crossref often lacks abstracts (e.g. Elsevier). OpenAlex usually has them. Best effort."""
    missing = [p for p in papers if not p["abstract"] and p.get("doi")]
    filled = 0
    for i in range(0, len(missing), 50):
        chunk = missing[i:i + 50]
        params = {
            "filter": "doi:" + "|".join(p["doi"] for p in chunk),
            "per-page": 50,
            "select": "doi,abstract_inverted_index",
        }
        if CONTACT:
            params["mailto"] = CONTACT
        if os.environ.get("OPENALEX_API_KEY"):
            params["api_key"] = os.environ["OPENALEX_API_KEY"]
        r = _get("https://api.openalex.org/works", params)
        if r is None:
            continue
        by_doi = {}
        for w in r.json().get("results", []):
            inv = w.get("abstract_inverted_index")
            if not inv or not w.get("doi"):
                continue
            words = sorted((pos, word) for word, poss in inv.items() for pos in poss)
            by_doi[w["doi"].lower().replace("https://doi.org/", "")] = " ".join(wd for _, wd in words)
        for p in chunk:
            if p["doi"] in by_doi:
                p["abstract"] = clean_text(by_doi[p["doi"]])
                filled += 1
        time.sleep(0.3)
    return filled


DC = "{http://purl.org/dc/elements/1.1/}"
PRISM = "{http://prismstandard.org/namespaces/basic/2.0/}"


def fetch_rss(journal: dict, since: date) -> list[dict]:
    """Generic RSS 2.0 reader (Wiley table-of-contents feeds, NBER new working papers, ...)."""
    r = _get(journal["rss"])
    if r is None:
        return []
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        print(f"  ! bad RSS for {journal['short']}")
        return []
    papers = []
    for it in root.iter("item"):
        title = clean_text(it.findtext("title"))
        authors: list[str] = []
        if journal.get("nber") and " -- by " in title:  # NBER: "Title -- by A, B, and C"
            title, by = title.rsplit(" -- by ", 1)
            authors = [a.strip() for a in re.split(r",\s*(?:and\s+)?|\s+and\s+", by) if a.strip()]
        if not title or SKIP_TITLES.match(title) or re.match(r"^(front|back)matter\b", title, re.I):
            continue
        authors = authors or [clean_text(c.text) for c in it.findall(f"{DC}creator") if c.text and c.text.strip()]
        if len(authors) == 1 and "," in authors[0]:
            authors = [a.strip() for a in re.split(r",\s*(?:and\s+)?|\s+and\s+", authors[0]) if a.strip()]
        raw_date = it.findtext(f"{DC}date") or it.findtext("pubDate") or ""
        d = None
        try:
            d = datetime.fromisoformat(raw_date[:10]).date() if re.match(r"\d{4}-\d\d-\d\d", raw_date) else \
                datetime.strptime(raw_date[5:16], "%d %b %Y").date()
        except ValueError:
            pass
        if d and d < since:
            continue
        doi = (it.findtext(f"{PRISM}doi") or it.findtext(f"{DC}identifier") or "").lower().strip() or None
        link = (it.findtext("link") or "").split("?af=")[0]
        desc = clean_text(it.findtext(f"{DC}description") or it.findtext("description"))
        if journal.get("name", "") in desc[:80]:  # Wiley puts the citation, not the abstract, in <description>
            desc = ""
        papers.append({
            "id": doi or link, "doi": doi, "title": title, "authors": authors, "abstract": desc,
            "url": f"https://doi.org/{doi}" if doi else link, "date": str(d) if d else None,
            "journal": journal["name"], "journal_short": journal["short"], "group": journal["group"],
            "tier": journal.get("tier", 3), "type": "preprint" if journal.get("group") == "Preprints" else "article",
        })
    return papers


ATOM = "{http://www.w3.org/2005/Atom}"


def fetch_arxiv(feed: dict, since: date) -> list[dict]:
    params = {
        "search_query": f"cat:{feed['category']}",
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": 300,
    }
    r = _get("https://export.arxiv.org/api/query", params)
    if r is None:
        return []
    papers = []
    root = ET.fromstring(r.content)
    for e in root.findall(f"{ATOM}entry"):
        published = (e.findtext(f"{ATOM}published") or "")[:10]
        if not published or datetime.strptime(published, "%Y-%m-%d").date() < since:
            continue
        # keep only papers whose PRIMARY category is this feed (avoids cross-list duplicates)
        prim = e.find("{http://arxiv.org/schemas/atom}primary_category")
        if prim is not None and prim.get("term") != feed["category"]:
            continue
        url = e.findtext(f"{ATOM}id") or ""
        arxiv_id = url.rsplit("/abs/", 1)[-1]
        papers.append({
            "id": f"arxiv:{re.sub(r'v[0-9]+$', '', arxiv_id)}",
            "doi": None,
            "title": clean_text(e.findtext(f"{ATOM}title")),
            "authors": [clean_text(a.findtext(f"{ATOM}name")) for a in e.findall(f"{ATOM}author")],
            "abstract": clean_text(e.findtext(f"{ATOM}summary")),
            "url": url.replace("http://", "https://"),
            "date": published,
            "journal": feed["name"],
            "journal_short": feed["category"],
            "group": feed.get("group", "Preprints"),
            "tier": 3,
            "type": "preprint",
        })
    return papers
