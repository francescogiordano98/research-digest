"""Render and send the weekly email. Uses plain SMTP (works with a Gmail app password, Outlook, SendGrid SMTP, ...)."""
from __future__ import annotations

import html
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path


def md_to_html(md: str) -> str:
    """Tiny Markdown subset: ### headings, **bold**, *italic*, paragraphs, '- ' lists, [text](url)."""
    out, in_list = [], False
    for block in re.split(r"\n\s*\n", md.strip()):
        lines = block.strip().splitlines()
        for line in lines:
            line = html.escape(line.strip(), quote=False)
            line = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2" style="color:#7a2e1d">\1</a>', line)
            line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
            line = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", line)
            if line.startswith("- "):
                if not in_list:
                    out.append('<ul style="padding-left:18px;margin:8px 0">')
                    in_list = True
                out.append(f'<li style="margin:4px 0">{line[2:]}</li>')
                continue
            if in_list:
                out.append("</ul>")
                in_list = False
            if line.startswith("#"):
                txt = line.lstrip("#").strip()
                out.append(f'<h3 style="font-family:Georgia,serif;font-size:17px;margin:18px 0 6px;color:#1d1b18">{txt}</h3>')
            elif line:
                out.append(f'<p style="margin:0 0 10px;line-height:1.55">{line}</p>')
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def render_email(issue: dict, site_url: str, topic_labels: dict[str, str], per_topic: int = 6) -> tuple[str, str]:
    subject = f"Research Digest No. {issue['number']} — {issue['date']} ({issue['count']} papers)"
    link = f"{site_url.rstrip('/')}/#issue={issue['date']}" if site_url else ""
    parts = [
        '<div style="background:#f6f3ee;padding:24px 12px"><div style="max-width:640px;margin:0 auto;background:#fffdf9;'
        'border:1px solid #e4ddd2;border-radius:10px;padding:28px 26px;font-family:-apple-system,Segoe UI,Helvetica,Arial,'
        'sans-serif;font-size:15px;color:#2b2824">',
        f'<div style="font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#7a2e1d">Weekly Research Digest · No. {issue["number"]}</div>',
        f'<h1 style="font-family:Georgia,serif;font-size:26px;margin:6px 0 4px">{html.escape(issue["date"])}</h1>',
        f'<div style="color:#776f64;font-size:13px;margin-bottom:18px">{issue["count"]} new papers from {issue["n_sources"]} outlets · '
        f'{html.escape(issue["window"])}</div>',
        md_to_html(issue["brief"]),
    ]
    seen = set()
    for tid, label in topic_labels.items():
        items = [p for p in issue["papers"] if p["topics"] and p["topics"][0] == tid and p["id"] not in seen][:per_topic]
        if not items:
            continue
        parts.append(f'<h2 style="font-family:Georgia,serif;font-size:19px;border-top:1px solid #e4ddd2;padding-top:16px;margin:24px 0 8px">{html.escape(label)}</h2>')
        for p in items:
            seen.add(p["id"])
            authors = ", ".join(p["authors"][:4]) + (" et al." if len(p["authors"]) > 4 else "")
            parts.append(
                f'<div style="margin:0 0 14px"><a href="{html.escape(p["url"])}" style="color:#1d1b18;font-weight:600;'
                f'text-decoration:none;font-size:15px">{html.escape(p["title"])}</a>'
                f'<div style="color:#776f64;font-size:12.5px;margin:2px 0">{html.escape(authors)} · <em>{html.escape(p["journal"])}</em></div>'
                f'<div style="font-size:14px;line-height:1.5">{html.escape(p.get("summary") or "")}</div></div>'
            )
    if link:
        parts.append(f'<p style="margin-top:24px"><a href="{link}" style="background:#7a2e1d;color:#fff;padding:10px 16px;'
                     f'border-radius:6px;text-decoration:none">See all {issue["count"]} papers →</a></p>')
    parts.append("</div></div>")
    return subject, "\n".join(parts)


def plain_text(issue: dict, site_url: str) -> str:
    lines = [f"Weekly Research Digest No. {issue['number']} - {issue['date']}", "", issue["brief"], ""]
    for p in issue["papers"][:30]:
        lines += [f"* {p['title']} ({p['journal']})", f"  {p.get('summary', '')}", f"  {p['url']}", ""]
    if site_url:
        lines.append(f"All papers: {site_url}")
    return "\n".join(lines)


def subscribers(root: Path) -> list[str]:
    emails = [e.strip() for e in os.environ.get("SUBSCRIBERS", "").replace(";", ",").split(",")]
    f = root / "config" / "subscribers.txt"  # local, git-ignored list (never published)
    if f.exists():
        emails += [l.strip() for l in f.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    return sorted({e for e in emails if "@" in e})


def send(issue: dict, site_url: str, topic_labels: dict[str, str], root: Path) -> None:
    host, user, pwd = os.environ.get("SMTP_HOST"), os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
    to = subscribers(root)
    if not (host and user and pwd):
        print("  email skipped: SMTP_HOST / SMTP_USER / SMTP_PASSWORD not set")
        return
    if not to:
        print("  email skipped: no subscribers")
        return
    port = int(os.environ.get("SMTP_PORT", "465"))
    sender = os.environ.get("MAIL_FROM", user)
    subject, body = render_email(issue, site_url, topic_labels)
    text = plain_text(issue, site_url)
    ctx = ssl.create_default_context()
    smtp = smtplib.SMTP_SSL(host, port, context=ctx) if port == 465 else smtplib.SMTP(host, port)
    with smtp as s:
        if port != 465:
            s.starttls(context=ctx)
        s.login(user, pwd)
        for addr in to:  # one message per subscriber so addresses are never exposed to each other
            msg = EmailMessage()
            msg["Subject"], msg["From"], msg["To"] = subject, sender, addr
            msg.set_content(text)
            msg.add_alternative(body, subtype="html")
            s.send_message(msg)
    print(f"  email sent to {len(to)} subscriber(s)")
