# Research Digest

A weekly digest of new research, in the spirit of Kevin Bryan's *Useful Knowledge*, tuned to:

- **Primary:** game & economic theory · innovation & entrepreneurship · industrial organization & AI
- **Secondary:** history · decision theory & behavior · AI/technology/energy/robotics/computation · physics & biology

Every Monday a GitHub Action:

1. pulls new articles from ~55 journals (via Crossref plus publisher RSS feeds), NBER working papers, and arXiv (econ.TH, cs.GT, econ.GN);
2. fills in missing abstracts from OpenAlex;
3. tags and ranks papers by topic keywords and venue;
4. *(with an Anthropic API key)* has Claude score each paper's relevance 0–10, write a one-sentence summary, and write the weekly editorial brief;
5. publishes the issue to the website (GitHub Pages), updates the RSS feed, and emails subscribers.

The website is an installable app (PWA): searchable (`AND`/`OR`/`NOT`, `"phrases"`, `author:`/`title:`/`journal:`), filterable by topic and outlet, with saved papers and offline reading.

```
config/sources.yaml   journals (ISSN, group, tier, RSS) — edit to add/remove outlets
config/topics.yaml    topics and keywords — edit to retune what's relevant
pipeline/             fetch → classify → summarize → publish → email
site/                 the web app (static files + data/issues/*.json)
.github/workflows/    weekly schedule
```

## Run locally

```bash
pip install -r requirements.txt
```

```bash
python pipeline/run_weekly.py --no-email --no-seen
```

```bash
python -m http.server 8765 --directory site
```

Then open http://localhost:8765. Flags: `--days N` (lookback window), `--no-ai`, `--no-email`, `--no-seen` (test run: doesn't mark papers as already shown). A preview of the email is written to `last_email.html`.

## Put it online (one-time, ~15 minutes)

1. **Create a GitHub repository** (public is simplest for free Pages), and push this folder to it:
   ```bash
   git init -b main && git add . && git commit -m "Research digest" && git remote add origin https://github.com/<you>/research-digest.git && git push -u origin main
   ```
2. **Enable Pages:** repo → Settings → Pages → *Source: GitHub Actions*.
3. **Set the site address:** Settings → Secrets and variables → Actions → *Variables* → `SITE_URL` = `https://<you>.github.io/research-digest/`.
4. **Add secrets** (Settings → Secrets and variables → Actions → *Secrets*):

   | Secret | Value |
   |---|---|
   | `ANTHROPIC_API_KEY` | from console.anthropic.com (optional but recommended — enables AI summaries and the brief; roughly $1/week) |
   | `SMTP_HOST` | `smtp.gmail.com` |
   | `SMTP_PORT` | `465` |
   | `SMTP_USER` | the Gmail address that sends the digest |
   | `SMTP_PASSWORD` | a Gmail **App Password** (Google Account → Security → 2‑Step Verification → App passwords) |
   | `SUBSCRIBERS` | comma-separated list, e.g. your own address |

5. **First run:** Actions → *Weekly digest* → *Run workflow*. After that it runs every Monday at 06:00 UTC.

Subscriber addresses live only in the secret (or the git-ignored `config/subscribers.txt` locally) — never in the published site. Each subscriber gets an individual email.

## Install on your phone

Open the site URL on the phone:

- **iPhone (Safari):** Share → *Add to Home Screen*.
- **Android (Chrome):** ⋮ → *Install app* (or the *Install app* button in the header).

It opens full-screen like a native app and keeps the latest issue available offline.

## Tuning

- Too many/few papers from big science outlets: set `MAX_GENERAL` (default 20) in the workflow env.
- A journal returns nothing: Crossref deposits can lag (e.g. Econometric Society journals); add the publisher's RSS feed with `rss:` in `sources.yaml`.
- Change the AI model with `DIGEST_MODEL` (default `claude-opus-5-5`).
