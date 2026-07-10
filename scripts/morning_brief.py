#!/usr/bin/env python3
"""
morning_brief.py — generate today's Morning Brief and write it into the daily note.

The brief surfaces what's happening today and what's waiting on the user:
  • Today's calendar events (from Vaults/Google Data/Calendar/)
  • Inbox needs reply: threads where the most recent message is from someone else,
    arrived >24h ago, and the user hasn't replied
  • Aging open commitments: items from yesterday's daily note's
    "Ideas & open questions" / "What didn't get done" sections, age > 0d

It writes a `<!-- brief:start --> ... <!-- brief:end -->` block at the top of
today's daily note (Vaults/Daily/<YYYY>/<MM>/<YYYY-MM-DD>.md), creating the note
if it doesn't exist. Idempotent: replaces an existing brief block in place.

Usage:
    python3 morning_brief.py                              # for today
    python3 morning_brief.py --date 2026-05-09            # for a specific date
    python3 morning_brief.py --vault-root /path/Second\ Brain/Vaults
    python3 morning_brief.py --dry-run                    # print brief, don't write
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone, timedelta, date as date_cls
from pathlib import Path
from typing import Optional

import lib_vault

ME_EMAIL = "you@example.com"

BRIEF_BEGIN = "<!-- brief:start -->"
BRIEF_END = "<!-- brief:end -->"

# ── Noreply / bulk-mail sender filter ──────────────────────────────────────────
# A thread whose last sender matches one of these patterns is excluded from
# "Inbox needs reply" — they are automated and cannot actually be replied to.
# Conventions match _scripts/clean_vault.py (MARKETING_LOCAL_PARTS / BULK_DOMAINS).

# Substrings that, when present anywhere in the sender's local-part, prove the
# address is automated. Tolerates things like `googleplay-noreply@...`,
# `notifications-noreply@...`, `no-reply-23li3-u0wiq_4brxdd-w7w@...`.
NORELY_LOCAL_SUBSTRINGS = (
    "noreply", "no-reply", "no_reply",
    "donotreply", "do-not-reply", "do_not_reply",
    "mailer-daemon",
)

# Local-parts that exact-match (case-insensitive) as bulk/automation senders.
# Mirrors _scripts/clean_vault.py MARKETING_LOCAL_PARTS so the brief and the
# cleaner agree on what counts as noise.
BULK_LOCAL_PARTS = {
    "bounce", "bounces",
    "news", "newsletter", "newsletters",
    "marketing", "promo", "promos", "deals", "offers",
    "unsubscribe", "mailings", "mailing",
    "notifications", "notification",
    "info", "updates", "update",
    "team", "hello", "hi", "support",
    "automated", "auto",
    "jobs",
    "inbox", "billing", "social", "alerts", "alert",
    "invitations", "invitation", "digest",
}

# Substrings that, when present anywhere in the local-part, mark the address as
# bulk/transactional even when it's a compound like `jobs-listings@`,
# `stories-recap@`, `invoice+statements@`, `notifications-x@`. Kept long and
# specific so real human role-accounts (e.g. `geral@`, `info@company.pt`) are
# NOT swept up by accident.
BULK_LOCAL_SUBSTRINGS = (
    "newsletter", "marketing", "promotion", "promo",
    "jobalert", "jobs-", "job-alert", "jobs-listings", "jobalerts",
    "notification",
    "stories-recap", "follow-suggestion",
    "statements", "invoice", "billing", "receipt",
    "digest", "mailer", "campaign",
)

# Domains/hosts that are essentially always bulk/transactional senders.
BULK_DOMAINS = {
    "mailchimp.com", "list-manage.com", "campaign-archive.com",
    "sendgrid.net", "sendgrid.com",
    "mailgun.org", "mailgun.net",
    "mandrillapp.com",
    "mktomail.com", "mkto-sp.com", "marketo.com",
    "hubspotemail.net", "hubspot.com", "hubspotstarter.com",
    "sendinblue.com", "sib.email",
    "constantcontact.com", "ccsend.com",
    "klaviyo.com", "klaviyomail.com",
    "amazonses.com",
    "postmarkapp.com",
    "intercom-mail.com", "intercom.io",
    "customer.io",
    "exacttarget.com", "exct.net",
    "rsgsv.net",
}

# Substrings inside the from-domain that indicate bulk/transactional. Covers
# whole domains and any of their sending subdomains (e.g. "vivino.com" also
# catches "m.vivino.com"). These are senders that never carry a repliable
# human message — newsletters, social/job notifications, course platforms,
# retail promos — so they should never appear in "Inbox needs reply".
BULK_DOMAIN_SUBSTRINGS = (
    "e.uber.com", "e.lyft.com", "e.airbnb", "e.booking", "e.shopify",
    "mailer.linkedin", "mailings.linkedin",
    "email.medium.com", "email.notion.so",
    "communication.coursera", "email.udemy",
    "promotional.", "marketing.", "newsletter.", "email.", "send.",
    ".bounces.google.com",
    # social / professional networks (always notifications, never humans)
    "linkedin.com", "instagram.com", "facebook.com", "facebookmail.com",
    "youtube.com", "twitter.com", "x.com",
    # newsletters / digests / retail / platforms seen in this vault
    "vivino.com", "aisecret.us", "westword-insider.com", "baubiologie.es",
    "brilliant.org", "skool.com", "feverup.com", "bandsintown.com",
    "flixbus.com", "zumub.com", "vidiq.com", "usebraintrust.com",
    "instructure.com", "revolut.com", "fidelidade.pt", "cegid.com",
    "meo.pt",
)


def _sender_is_bulk_or_noreply(sender: str) -> bool:
    """True if the sender address looks automated / bulk-mail and should not
    appear in 'Inbox needs reply'. Conservative — matches obvious patterns
    only. Real humans with addresses like `hello@personaldomain.com` will be
    over-filtered occasionally; that's the right trade-off because they're
    overwhelmingly bulk in practice."""
    if not sender or "@" not in sender:
        return False
    s = sender.lower().strip()
    local, _, domain = s.partition("@")
    # noreply substrings
    for needle in NORELY_LOCAL_SUBSTRINGS:
        if needle in local:
            return True
    # bulk local-part (exact match)
    if local in BULK_LOCAL_PARTS:
        return True
    # bulk local-part (substring match — compound automated addresses)
    for needle in BULK_LOCAL_SUBSTRINGS:
        if needle in local:
            return True
    # bulk-mail domains
    if domain in BULK_DOMAINS:
        return True
    for needle in BULK_DOMAIN_SUBSTRINGS:
        if needle in domain:
            return True
    return False

MONTH_DIR = {
    1: "01-Jan", 2: "02-Feb", 3: "03-Mar", 4: "04-Apr",
    5: "05-May", 6: "06-Jun", 7: "07-Jul", 8: "08-Aug",
    9: "09-Sep", 10: "10-Oct", 11: "11-Nov", 12: "12-Dec",
}


# ── Helpers ────────────────────────────────────────────────────────────────────


def parse_frontmatter(text: str) -> dict:
    return lib_vault.frontmatter_dict(text)


def daily_note_path(vault_root: Path, day: date_cls) -> Path:
    return (vault_root / "Daily" / str(day.year)
            / f"{day.month:02d}" / f"{day.strftime('%Y-%m-%d')}.md")


# ── Today's events ─────────────────────────────────────────────────────────────


def collect_today_events(vault_root: Path, day: date_cls) -> list[dict]:
    """Read Calendar/<YYYY>/<MM-Mon>/ and return events whose `date:` matches `day`."""
    cal_dir = (vault_root / "Google Data" / "Calendar"
               / str(day.year) / MONTH_DIR[day.month])
    if not cal_dir.exists():
        return []
    events = []
    for f in sorted(cal_dir.glob("*.md")):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
            fm = parse_frontmatter(text)
        except Exception:
            continue
        if fm.get("date") != day.strftime("%Y-%m-%d"):
            continue
        if fm.get("status") == "cancelled":
            continue
        events.append({
            "stem": f.stem,
            "summary": fm.get("summary", "(no title)"),
            "time": fm.get("time", ""),
            "end_time": fm.get("end_time", ""),
            "location": fm.get("location", ""),
            "conference_url": fm.get("conference_url", ""),
            "my_response": fm.get("my_response", ""),
            "calendar": fm.get("calendar", ""),
        })
    events.sort(key=lambda e: e["time"])
    return events


# ── Inbox needs reply ──────────────────────────────────────────────────────────


def collect_inbox_needs_reply(vault_root: Path, now_utc: datetime,
                              recent_window_days: int = 14) -> list[dict]:
    """
    Scan Threads/*.md MOCs. A thread needs a reply if:
      - Its last message is from someone other than the user
      - The last message is at least 24h old
      - The last message is within the last `recent_window_days` (default 14)
      - the user has not replied since (i.e., no message from the user with date >= last_message)
    The Thread MOC's lines look like:
        - YYYY-MM-DD — sender@x — [[link|preview]]
    """
    threads_dir = vault_root / "Google Data" / "Threads"
    if not threads_dir.exists():
        return []

    line_re = re.compile(
        r"^\s*-\s+(\d{4}-\d{2}-\d{2})\s+—\s+(\S+@\S+)\s+—\s+\[\[(.+?)(\|.*)?\]\]"
    )
    out: list[dict] = []
    cutoff = now_utc - timedelta(hours=24)
    floor = now_utc - timedelta(days=recent_window_days)
    today_str = now_utc.strftime("%Y-%m-%d")

    # First pass — parse every thread once AND build a counterparty→reply map.
    #
    # Threads are keyed by subject-slug, not by Gmail thread-id, so one real
    # conversation can be split across several files when a counterparty edits
    # the subject or sends a separate calendar invite. The original same-file
    # reply check then misses replies that landed in a sibling file, and the
    # orphaned file nags "needs reply" forever. `me_last_to[addr]` records the
    # most recent date the user sent to a given counterparty across ALL threads, so
    # we can recognise a reply even when it lives in a different file.
    parsed: list[tuple] = []
    me_last_to: dict[str, str] = {}
    for f in threads_dir.glob("*.md"):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        msgs = []
        for line in text.splitlines():
            m = line_re.match(line)
            if m:
                msgs.append({
                    "date": m.group(1),
                    "sender": m.group(2).lower(),
                    "link": m.group(3),
                })
        if not msgs:
            continue
        msgs.sort(key=lambda x: x["date"])
        counterparties = {x["sender"] for x in msgs if x["sender"] != ME_EMAIL}
        me_dates = [x["date"] for x in msgs if x["sender"] == ME_EMAIL]
        if me_dates and counterparties:
            latest = max(me_dates)
            for c in counterparties:
                if latest > me_last_to.get(c, ""):
                    me_last_to[c] = latest
        parsed.append((f, text, msgs))

    for f, text, msgs in parsed:
        last = msgs[-1]
        if last["sender"] == ME_EMAIL:
            continue
        # Skip noreply / bulk-mail senders — these cannot be replied to and
        # are noise in the brief.
        if _sender_is_bulk_or_noreply(last["sender"]):
            continue
        # ≥24h old
        try:
            last_dt = datetime.strptime(last["date"], "%Y-%m-%d").replace(
                tzinfo=timezone.utc, hour=23, minute=59
            )
        except Exception:
            continue
        if last_dt > cutoff:
            continue
        if last_dt < floor:
            # too old — dead thread, not actively waiting on a reply
            continue
        # Has the user replied since, in THIS thread file?
        me_reply = any(
            m["sender"] == ME_EMAIL and m["date"] >= last["date"]
            for m in msgs
        )
        if me_reply:
            continue
        # Cross-thread reply: did the user email this same counterparty on/after the
        # last inbound date in any OTHER thread? Catches split conversations
        # (subject edits, separate calendar invites) so we don't nag about a
        # message he already answered under a different subject line.
        replied_elsewhere = me_last_to.get(last["sender"], "")
        if replied_elsewhere >= last["date"]:
            continue

        # Pull the thread subject from frontmatter for nicer display
        fm = parse_frontmatter(text)
        subject = fm.get("subject", f.stem)
        age_days = (now_utc.date() - datetime.strptime(last["date"], "%Y-%m-%d").date()).days
        out.append({
            "subject": subject,
            "thread_stem": f.stem,
            "from": last["sender"],
            "last_date": last["date"],
            "age_days": age_days,
        })

    out.sort(key=lambda x: x["age_days"], reverse=True)
    return out[:10]  # cap noise


# ── Aging commitments ──────────────────────────────────────────────────────────


def _last_n_daily_notes(vault_root: Path, day: date_cls, n: int = 7) -> list[Path]:
    out = []
    for i in range(1, n + 1):
        d = day - timedelta(days=i)
        p = daily_note_path(vault_root, d)
        if p.exists():
            out.append(p)
    return out


def _build_me_last_to(threads_dir: Path, line_re) -> dict[str, str]:
    """Map counterparty address → most recent date (YYYY-MM-DD) the user emailed
    them, across ALL thread files. Because threads are keyed by subject-slug
    rather than Gmail thread-id, one conversation can be split across several
    files; this map lets us recognise that the user has answered a given person
    even when his reply lives in a sibling file under a different subject."""
    me_last_to: dict[str, str] = {}
    for f in threads_dir.glob("*.md"):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        msgs = []
        for line in text.splitlines():
            m = line_re.match(line)
            if m:
                msgs.append({"date": m.group(1), "sender": m.group(2).lower()})
        if not msgs:
            continue
        counterparties = {x["sender"] for x in msgs if x["sender"] != ME_EMAIL}
        me_dates = [x["date"] for x in msgs if x["sender"] == ME_EMAIL]
        if me_dates and counterparties:
            latest = max(me_dates)
            for c in counterparties:
                if latest > me_last_to.get(c, ""):
                    me_last_to[c] = latest
    return me_last_to


def collect_resolved_thread_topics(vault_root: Path,
                                   recent_window_days: int = 30) -> list[dict]:
    """
    Return threads that are 'resolved from your side' — i.e. the ball is in
    the OTHER party's court — for the purpose of suppressing stale 'reply to X'
    commitments.

    A thread counts as resolved if EITHER its own most-recent message is from
    the user, OR the user emailed that thread's counterparty on/after the thread's last
    inbound date in some OTHER thread file (handles conversations split across
    subject-keyed files, e.g. a recruiter changing the subject or sending a
    separate calendar invite).

    Returns a list of {subject, slug, last_date, last_sender} dicts. Only
    includes threads active within the last `recent_window_days`.
    """
    threads_dir = vault_root / "Google Data" / "Threads"
    if not threads_dir.exists():
        return []
    line_re = re.compile(
        r"^\s*-\s+(\d{4}-\d{2}-\d{2})\s+—\s+(\S+@\S+)\s+—\s+\[\[(.+?)(\|.*)?\]\]"
    )
    floor = datetime.now(timezone.utc) - timedelta(days=recent_window_days)
    me_last_to = _build_me_last_to(threads_dir, line_re)
    out: list[dict] = []
    for f in threads_dir.glob("*.md"):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        msgs = []
        for line in text.splitlines():
            m = line_re.match(line)
            if m:
                msgs.append({"date": m.group(1), "sender": m.group(2).lower()})
        if not msgs:
            continue
        msgs.sort(key=lambda x: x["date"])
        last = msgs[-1]
        try:
            last_dt = datetime.strptime(last["date"], "%Y-%m-%d").replace(
                tzinfo=timezone.utc, hour=23, minute=59,
            )
        except Exception:
            continue
        if last_dt < floor:
            continue
        # Resolved if the user sent last here, OR he answered this counterparty
        # on/after the last inbound date somewhere else.
        if last["sender"] == ME_EMAIL:
            pass
        elif me_last_to.get(last["sender"], "") >= last["date"]:
            pass
        else:
            continue
        fm = parse_frontmatter(text)
        subject = (fm.get("subject", f.stem) or "").strip().strip('"').strip("'")
        out.append({
            "subject": subject,
            "slug": f.stem,
            "last_date": last["date"],
            "last_sender": last["sender"],
        })
    return out


def _bullet_matches_resolved_thread(bullet_text: str,
                                    resolved: list[dict]) -> Optional[dict]:
    """
    Return the resolved-thread entry whose subject (or slug) is referenced in
    the bullet, else None. We require a meaningful overlap — the thread
    subject (lowercased, stripped) appears verbatim in the bullet, OR the slug
    appears. Short subjects (<8 chars) are skipped to avoid noise.
    """
    if not bullet_text:
        return None
    bt = bullet_text.lower()
    for r in resolved:
        subj = (r.get("subject") or "").lower().strip()
        slug = (r.get("slug") or "").lower().strip()
        if subj and len(subj) >= 8 and subj in bt:
            return r
        if slug and len(slug) >= 8 and slug.replace("-", " ") in bt:
            return r
    return None


def collect_aging_commitments(vault_root: Path, day: date_cls) -> list[dict]:
    """
    Walk the last 7 daily notes and pull bullets from the
    'Ideas & open questions' and (in weekly notes) 'What didn't get done' sections.
    Items dated more than 0 days ago are 'aging'.

    Suppress bullets whose underlying thread is now 'ball in other party's
    court' — i.e., the named thread's most-recent message is from the user. This
    prevents stale 'Reply to X about Y' bullets from rolling forward after
    the user has actually replied.
    """
    out = []
    notes = _last_n_daily_notes(vault_root, day, n=7)
    for p in notes:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        fm = parse_frontmatter(text)
        note_date = fm.get("date") or p.stem
        try:
            note_d = datetime.strptime(note_date, "%Y-%m-%d").date()
        except ValueError:
            continue
        # extract Ideas & open questions section
        section = re.search(
            r"^##\s+Ideas\s*&\s*open\s+questions\s*\n(.*?)(?=^##\s|\Z)",
            text, re.IGNORECASE | re.MULTILINE | re.DOTALL,
        )
        if not section:
            continue
        for bullet in re.finditer(r"^\s*-\s+(.+?)(?=\n\s*-\s|\n\s*\n|\Z)",
                                  section.group(1), re.DOTALL | re.MULTILINE):
            text_item = bullet.group(1).strip().replace("\n", " ")
            text_item = re.sub(r"\s+", " ", text_item)
            if len(text_item) < 5:
                continue
            age = (day - note_d).days
            out.append({
                "text": text_item[:240],
                "from_date": note_date,
                "age_days": age,
            })
    # de-dup by text
    seen = set()
    deduped = []
    for it in out:
        key = it["text"][:80]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)

    # Suppress bullets whose referenced thread is now 'resolved from your side'.
    resolved_threads = collect_resolved_thread_topics(vault_root)
    filtered = []
    for it in deduped:
        match = _bullet_matches_resolved_thread(it["text"], resolved_threads)
        if match is not None:
            # skip — the user already replied; the bullet is stale
            continue
        filtered.append(it)

    filtered.sort(key=lambda x: x["age_days"], reverse=True)
    return filtered[:10]


# ── Brief renderer ─────────────────────────────────────────────────────────────


def render_brief(day: date_cls, events: list[dict],
                 inbox: list[dict], commitments: list[dict]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [BRIEF_BEGIN]
    lines.append(f"## Morning Brief — {day.strftime('%A, %B %d')}")
    lines.append(f"_Generated {now}._")
    lines.append("")

    if events:
        lines.append("### 📅 Today's calendar")
        for e in events:
            t = e["time"]
            if e["end_time"]:
                t = f"{t}–{e['end_time']}"
            extras = []
            if e["my_response"] and e["my_response"] != "accepted":
                extras.append(f"RSVP: {e['my_response']}")
            if e["location"]:
                extras.append(e["location"])
            if e["conference_url"]:
                extras.append(f"[meet]({e['conference_url']})")
            extras_str = f" — {', '.join(extras)}" if extras else ""
            lines.append(f"- **{t}** [[{e['stem']}|{e['summary']}]]{extras_str}")
        lines.append("")
    else:
        lines.append("### 📅 Today's calendar")
        lines.append("_Nothing on the calendar._")
        lines.append("")

    if inbox:
        lines.append("### 📨 Inbox needs reply")
        for t in inbox:
            lines.append(f"- **{t['age_days']}d** — {t['from']} — [[{t['thread_stem']}|{t['subject']}]]")
        lines.append("")
    else:
        lines.append("### 📨 Inbox needs reply")
        lines.append("_Nothing waiting on you (or last sync hasn't run yet)._")
        lines.append("")

    if commitments:
        lines.append("### 🧷 Open commitments aging")
        for c in commitments:
            lines.append(f"- **{c['age_days']}d** ({c['from_date']}) — {c['text']}")
        lines.append("")

    lines.append(BRIEF_END)
    return "\n".join(lines)


# ── Daily-note insertion ───────────────────────────────────────────────────────


def insert_or_replace_brief(daily_path: Path, brief: str) -> str:
    """
    If the daily note doesn't exist, create a minimal one with the brief at top.
    If it exists and already has a BRIEF block, replace it.
    Else inject the brief right after the frontmatter / H1.
    Returns 'created' | 'replaced' | 'inserted'.
    """
    if not daily_path.exists():
        daily_path.parent.mkdir(parents=True, exist_ok=True)
        d = daily_path.stem
        contents = (
            "---\n"
            "type: daily\n"
            f"date: {d}\n"
            "tags:\n  - daily\n"
            f"last_updated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')}\n"
            "---\n\n"
            f"# {d} — {datetime.strptime(d, '%Y-%m-%d').strftime('%A')}\n\n"
            f"{brief}\n"
        )
        daily_path.write_text(contents, encoding="utf-8")
        return "created"

    text = daily_path.read_text(encoding="utf-8")
    if BRIEF_BEGIN in text and BRIEF_END in text:
        # replace
        new_text = re.sub(
            r"<!-- brief:start -->.*?<!-- brief:end -->",
            brief,
            text,
            count=1,
            flags=re.DOTALL,
        )
        daily_path.write_text(new_text, encoding="utf-8")
        return "replaced"

    # Insert: after the first H1 (or after frontmatter if no H1)
    h1 = re.search(r"^#\s.+\n", text, re.MULTILINE)
    if h1:
        idx = h1.end()
        new_text = text[:idx] + "\n" + brief + "\n" + text[idx:]
    else:
        # find end of frontmatter
        fm_end = text.find("\n---\n", 3)
        if fm_end != -1:
            idx = fm_end + len("\n---\n")
            new_text = text[:idx] + "\n" + brief + "\n" + text[idx:]
        else:
            new_text = brief + "\n" + text
    daily_path.write_text(new_text, encoding="utf-8")
    return "inserted"


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD; default today (local)")
    ap.add_argument("--vault-root", default=str(lib_vault.vaults_root()),
                    help="Path to the Vaults/ root")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    vault_root = Path(args.vault_root)
    if not vault_root.exists():
        print(f"ERROR: vault root {vault_root} does not exist", file=sys.stderr)
        return 2

    if args.date:
        day = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        day = datetime.now().date()

    now_utc = datetime.now(timezone.utc)
    events = collect_today_events(vault_root, day)
    inbox = collect_inbox_needs_reply(vault_root, now_utc)
    commitments = collect_aging_commitments(vault_root, day)

    brief = render_brief(day, events, inbox, commitments)

    daily_path = daily_note_path(vault_root, day)

    if args.dry_run:
        print(f"# DRY-RUN — would write to {daily_path}")
        print(brief)
        return 0

    action = insert_or_replace_brief(daily_path, brief)

    print(
        f"Brief {action} at {daily_path.relative_to(vault_root.parent)} — "
        f"{len(events)} event(s), {len(inbox)} inbox item(s), "
        f"{len(commitments)} commitment(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
