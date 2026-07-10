#!/usr/bin/env python3
"""
gmail_sync.py — incremental Gmail-to-Obsidian-vault sync.

This script does NOT call the Gmail MCP itself. It reads a JSON file of
threads (produced by Claude in the orchestration step) and writes each
message into the existing `Vaults/Google Data/` layout:

    Gmail/<YYYY>/<MM-Mon>/<YYYY-MM-DD subject>.md       — primary mail
    Gmail/_Quarantine/<YYYY>/<MM-Mon>/<...>.md          — promo/social/etc.
    Contacts/<Display Name>.md                          — incrementally appended
    Threads/<thread-slug>.md                            — incrementally appended
    Topics/<Label Name>.md                              — incrementally appended

Idempotent: re-running with the same JSON is a no-op (existing files are
skipped). Cursor state is persisted to --state.

JSON input shape (one entry per thread):
[
  {
    "bucket": "primary" | "sent" | "promotions" | "social" | "updates" | "forums",
    "thread": {
      "id": "<thread_id>",
      "messages": [
        {
          "id": "<message_id>",
          "date": "2026-05-09T13:21:07Z",
          "sender": "Name <a@b.com>" | "a@b.com",
          "toRecipients": ["x@y.com", ...],
          "subject": "...",
          "snippet": "...",
          "plaintextBody": "..."
        },
        ...
      ]
    }
  },
  ...
]

State file shape (--state):
{
  "last_sync_at": "2026-05-09T16:00:00Z",
  "seen_message_ids": ["<id>", "<id>", ...]   // capped at last 2000
}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Constants ──────────────────────────────────────────────────────────────────

ME_EMAIL = "you@example.com"
ME_DISPLAY = "the user Miguel Urdaneta (me)"

MONTH_DIR = {
    1: "01-Jan", 2: "02-Feb", 3: "03-Mar", 4: "04-Apr",
    5: "05-May", 6: "06-Jun", 7: "07-Jul", 8: "08-Aug",
    9: "09-Sep", 10: "10-Oct", 11: "11-Nov", 12: "12-Dec",
}

QUARANTINE_BUCKETS = {"promotions", "social", "updates", "forums"}
PRIMARY_BUCKETS = {"primary", "sent", "important", "personal"}

BUCKET_TO_LABEL = {
    "primary": "Inbox",
    "sent": "Sent",
    "important": "Important",
    "promotions": "Category Promotions",
    "social": "Category Social",
    "updates": "Category Updates",
    "forums": "Category Forums",
}

# ── Utilities ──────────────────────────────────────────────────────────────────

EMAIL_RE = re.compile(r"<([^>]+)>")
INVALID_PATH_CHARS = re.compile(r'[\\/:*?"<>|]')
WS = re.compile(r"\s+")


def safe_filename(s: str, maxlen: int = 100) -> str:
    s = INVALID_PATH_CHARS.sub("_", s or "")
    s = WS.sub(" ", s).strip()
    return s[:maxlen] or "untitled"


def parse_sender(raw: str) -> tuple[str, str]:
    """Return (display_name, email) from 'Name <a@b.com>' or 'a@b.com'."""
    if not raw:
        return ("Unknown", "unknown@unknown")
    m = EMAIL_RE.search(raw)
    if m:
        email = m.group(1).strip().lower()
        name = raw[: m.start()].strip().strip('"').strip()
        if not name:
            name = email.split("@", 1)[0]
        return (name, email)
    # bare email
    return (raw.split("@", 1)[0], raw.strip().lower())


def slugify_subject(subject: str) -> str:
    """Convention used by the existing thread MOCs: lowercase, hyphenated, strip Re:/Fwd:."""
    s = subject or "no-subject"
    s = re.sub(r"^\s*(re|fwd|fw):\s*", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"^\s*(re|fwd|fw):\s*", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE).strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:80] or "no-subject"


def parse_date(date_str: str) -> datetime:
    # Gmail MCP gives us ISO 8601 with Z
    if date_str.endswith("Z"):
        return datetime.fromisoformat(date_str[:-1]).replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(date_str)


def yaml_escape(s: str) -> str:
    """Conservative YAML string-escape (always quote, escape backslash and quote)."""
    if s is None:
        return ""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def yaml_list(items: list[str]) -> str:
    if not items:
        return "[]"
    return "\n  - " + "\n  - ".join(yaml_escape(i) for i in items)


# ── Note builders ──────────────────────────────────────────────────────────────


def build_email_note(msg: dict, bucket: str, thread_subject_slug: str) -> str:
    sender_raw = msg.get("sender", "")
    display, email = parse_sender(sender_raw)
    dt = parse_date(msg["date"])
    date_str = dt.strftime("%Y-%m-%d")
    time_str = dt.strftime("%H:%M")
    subject = msg.get("subject", "(no subject)")
    body = msg.get("plaintextBody") or msg.get("snippet", "")
    to_list = msg.get("toRecipients", []) or []

    label = BUCKET_TO_LABEL.get(bucket, "Inbox")
    labels = [label]

    is_sent = email == ME_EMAIL or bucket == "sent"

    front = [
        "---",
        f"date: {date_str}",
        f'time: "{time_str}"',
        f"from: {yaml_escape(sender_raw)}",
        f'to: {yaml_escape(", ".join(to_list))}',
        f'cc: ""',
        f"subject: {yaml_escape(subject)}",
        f"labels:{yaml_list(labels)}",
        f"message_id: {yaml_escape(msg.get('id', ''))}",
        "type: email",
        "---",
        "",
    ]

    body_lines = [
        f"# {subject}",
        "",
        body.rstrip(),
        "",
    ]

    # Connections
    contact_link = (
        f"[[{ME_DISPLAY}|me]]" if is_sent else f"[[{display}]]"
    )
    direction = "**To:**" if is_sent else "**From:**"
    if is_sent:
        # 'To' is the first recipient if known
        recipient_display = (
            to_list[0].split("@")[0] if to_list else "unknown"
        )
        connections = [
            "<!-- connections:start -->",
            "## Connections",
            "",
            f"- **To:** [[{recipient_display}]]",
            f"- **From:** {contact_link}",
        ]
    else:
        connections = [
            "<!-- connections:start -->",
            "## Connections",
            "",
            f"- **From:** {contact_link}",
        ]

    if thread_subject_slug:
        connections.append(f"- **Thread:** [[{thread_subject_slug}]]")

    if bucket in QUARANTINE_BUCKETS:
        connections.append(f"- **Topics:** [[{label}]]")

    connections.append("<!-- connections:end -->")
    connections.append("")

    return "\n".join(front + body_lines + connections)


def email_path(vault: Path, msg: dict, bucket: str, suffix: int = 0) -> Path:
    dt = parse_date(msg["date"])
    date_str = dt.strftime("%Y-%m-%d")
    subject = msg.get("subject", "(no subject)")
    base_name = f"{date_str} {subject.replace('/', '_').replace(':', '_')}"
    if suffix:
        base_name = f"{base_name} ({suffix})"
    base_name = safe_filename(base_name) + ".md"

    if bucket in QUARANTINE_BUCKETS:
        return vault / "Gmail" / "_Quarantine" / str(dt.year) / MONTH_DIR[dt.month] / base_name
    else:
        return vault / "Gmail" / str(dt.year) / MONTH_DIR[dt.month] / base_name


def write_email(vault: Path, msg: dict, bucket: str, thread_subject_slug: str) -> Optional[Path]:
    """Write an email note. Returns the path written, or None if it already existed."""
    suffix = 0
    while True:
        p = email_path(vault, msg, bucket, suffix=suffix)
        if not p.exists():
            break
        # If the existing file is the SAME message_id, skip silently; otherwise bump suffix.
        try:
            existing = p.read_text(encoding="utf-8", errors="ignore")
            if msg.get("id", "") and msg.get("id") in existing:
                return None
        except Exception:
            pass
        suffix += 1
        if suffix > 200:
            return None  # safety
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(build_email_note(msg, bucket, thread_subject_slug), encoding="utf-8")
    return p


# ── MOC updaters (Contact / Thread / Topic) ────────────────────────────────────


def update_contact(vault: Path, display: str, email: str, dt: datetime, link_text: str) -> bool:
    """Append/create a Contact note. Returns True if newly created."""
    if email == ME_EMAIL:
        return False  # don't create a contact for self
    p = vault / "Contacts" / f"{safe_filename(display)}.md"
    year = str(dt.year)
    date_str = dt.strftime("%Y-%m-%d")
    line = f"- {date_str} ← [[{link_text}]]"
    created = False

    if not p.exists():
        created = True
        domain = email.split("@", 1)[1] if "@" in email else ""
        contents = (
            "---\n"
            "type: contact\n"
            f"name: {yaml_escape(display)}\n"
            f"email: {email}\n"
            f"first_email: {date_str}\n"
            f"last_email: {date_str}\n"
            "email_count: 1\n"
            f"domain: {domain}\n"
            "tags:\n"
            "  - contact\n"
            "---\n\n"
            f"# {display}\n\n"
            f"**Email:** {email}\n"
            f"**Period:** {date_str} → {date_str}\n"
            f"**Emails exchanged:** 1\n\n"
            f"## Conversations\n\n"
            f"### {year}\n\n"
            f"{line}\n"
        )
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(contents, encoding="utf-8")
        return True

    # Append to existing
    text = p.read_text(encoding="utf-8")
    if line in text:
        return False  # already present

    # Find/insert under "### <year>" heading; create heading if missing
    year_heading = f"### {year}"
    if year_heading in text:
        # append at end of that section (right before the next ### or EOF)
        idx = text.index(year_heading)
        # find next ### after the year heading
        next_idx = text.find("\n### ", idx + len(year_heading))
        if next_idx == -1:
            text = text.rstrip() + "\n" + line + "\n"
        else:
            text = text[:next_idx].rstrip() + "\n" + line + "\n\n" + text[next_idx:].lstrip()
    else:
        # append a new year section at the end
        text = text.rstrip() + f"\n\n{year_heading}\n\n{line}\n"

    # update count + last_email in frontmatter
    text = re.sub(r"^email_count:\s*(\d+)\s*$",
                  lambda m: f"email_count: {int(m.group(1)) + 1}",
                  text, count=1, flags=re.MULTILINE)
    text = re.sub(r"^last_email:.*$", f"last_email: {date_str}",
                  text, count=1, flags=re.MULTILINE)
    text = re.sub(r"\*\*Emails exchanged:\*\*\s*\d+",
                  lambda m: re.sub(r"\d+",
                                   str(int(re.search(r"\d+", m.group(0)).group(0)) + 1),
                                   m.group(0)),
                  text, count=1)

    p.write_text(text, encoding="utf-8")
    return False


def update_thread(vault: Path, subject: str, dt: datetime, sender_email: str, link_text: str, display_subject: str) -> bool:
    """Append/create a Thread MOC. Returns True if newly created."""
    slug = slugify_subject(subject)
    p = vault / "Threads" / f"{slug}.md"
    date_str = dt.strftime("%Y-%m-%d")
    line = f"- {date_str} — {sender_email} — [[{link_text}|{display_subject[:60]}]]"
    created = False

    if not p.exists():
        created = True
        contents = (
            "---\n"
            "type: thread\n"
            f"subject: {yaml_escape(subject)}\n"
            f"first_message: {date_str}\n"
            f"last_message: {date_str}\n"
            "message_count: 1\n"
            "tags:\n"
            "  - thread\n"
            "---\n\n"
            f"# {subject}\n\n"
            f"**Messages:** 1\n"
            f"**Period:** {date_str} → {date_str}\n\n"
            f"## Participants\n\n"
            f"- {sender_email}\n\n"
            f"## Messages\n\n"
            f"{line}\n"
        )
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(contents, encoding="utf-8")
        return True

    # Append
    text = p.read_text(encoding="utf-8")
    if link_text in text:
        return False

    if "## Messages" in text:
        text = text.rstrip() + "\n" + line + "\n"
    else:
        text = text.rstrip() + "\n\n## Messages\n\n" + line + "\n"

    # bump count + last_message
    text = re.sub(r"^message_count:\s*(\d+)\s*$",
                  lambda m: f"message_count: {int(m.group(1)) + 1}",
                  text, count=1, flags=re.MULTILINE)
    text = re.sub(r"^last_message:.*$", f"last_message: {date_str}",
                  text, count=1, flags=re.MULTILINE)
    text = re.sub(r"\*\*Messages:\*\*\s*\d+",
                  lambda m: re.sub(r"\d+",
                                   str(int(re.search(r"\d+", m.group(0)).group(0)) + 1),
                                   m.group(0)),
                  text, count=1)

    p.write_text(text, encoding="utf-8")
    return False


def update_topic(vault: Path, label: str, dt: datetime, sender_email: str, link_text: str, display_subject: str) -> bool:
    p = vault / "Topics" / f"{safe_filename(label)}.md"
    year = str(dt.year)
    date_str = dt.strftime("%Y-%m-%d")
    line = f"- {date_str} — {sender_email} — [[{link_text}|{display_subject[:60]}]]"
    created = False

    if not p.exists():
        created = True
        contents = (
            "---\n"
            "type: topic\n"
            f"label: {yaml_escape(label)}\n"
            "email_count: 1\n"
            "tags:\n"
            "  - topic\n"
            "---\n\n"
            f"# {label}\n\n"
            "**Emails:** 1\n\n"
            f"## {year}\n\n"
            f"{line}\n"
        )
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(contents, encoding="utf-8")
        return True

    text = p.read_text(encoding="utf-8")
    if link_text in text:
        return False
    year_heading = f"## {year}"
    if year_heading in text:
        idx = text.index(year_heading)
        next_idx = text.find("\n## ", idx + len(year_heading))
        if next_idx == -1:
            text = text.rstrip() + "\n" + line + "\n"
        else:
            text = text[:next_idx].rstrip() + "\n" + line + "\n\n" + text[next_idx:].lstrip()
    else:
        text = text.rstrip() + f"\n\n{year_heading}\n\n{line}\n"

    text = re.sub(r"^email_count:\s*(\d+)\s*$",
                  lambda m: f"email_count: {int(m.group(1)) + 1}",
                  text, count=1, flags=re.MULTILINE)
    text = re.sub(r"\*\*Emails:\*\*\s*\d+",
                  lambda m: re.sub(r"\d+",
                                   str(int(re.search(r"\d+", m.group(0)).group(0)) + 1),
                                   m.group(0)),
                  text, count=1)

    p.write_text(text, encoding="utf-8")
    return False


# ── Main ───────────────────────────────────────────────────────────────────────


def load_state(state_path: Path) -> dict:
    if state_path.exists():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"last_sync_at": None, "seen_message_ids": []}


def save_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", required=True, help="JSON file: list of {bucket, thread} entries")
    ap.add_argument("--vault", required=True, help="Path to Vaults/Google Data/")
    ap.add_argument("--state", required=True, help="Cursor state JSON path")
    ap.add_argument("--dry-run", action="store_true", help="Print what would be written, no file changes")
    args = ap.parse_args()

    threads_path = Path(args.threads)
    vault = Path(args.vault)
    state_path = Path(args.state)

    if not threads_path.exists():
        print(f"ERROR: {threads_path} does not exist", file=sys.stderr)
        return 2

    if not vault.exists():
        print(f"ERROR: vault {vault} does not exist", file=sys.stderr)
        return 2

    payload = json.loads(threads_path.read_text(encoding="utf-8"))
    state = load_state(state_path)
    seen = set(state.get("seen_message_ids", []))

    counts = {
        "emails_written": 0,
        "skipped_existing": 0,
        "skipped_seen": 0,
        "contacts_created": 0,
        "threads_created": 0,
        "topics_created": 0,
    }

    new_seen = []

    for entry in payload:
        bucket = entry.get("bucket", "primary")
        thread = entry.get("thread") or {}
        messages = thread.get("messages", [])
        if not messages:
            continue

        # use the FIRST message's subject (canonical Gmail thread subject) as thread anchor
        thread_subject = messages[0].get("subject", "(no subject)")
        thread_slug = slugify_subject(thread_subject)

        for msg in messages:
            mid = msg.get("id", "")
            if mid in seen:
                counts["skipped_seen"] += 1
                continue

            display, email = parse_sender(msg.get("sender", ""))
            try:
                dt = parse_date(msg["date"])
            except Exception:
                continue

            if args.dry_run:
                p = email_path(vault, msg, bucket)
                print(f"[dry-run] {bucket}: {p.relative_to(vault.parent.parent) if vault.parent.parent in p.parents else p}")
                new_seen.append(mid)
                continue

            written = write_email(vault, msg, bucket, thread_slug)
            if written is None:
                counts["skipped_existing"] += 1
                new_seen.append(mid)
                continue

            counts["emails_written"] += 1
            new_seen.append(mid)

            # Build the link_text for MOCs (filename without .md)
            link_text = written.stem

            # Contact: skip if I am the sender (sent mail)
            if email != ME_EMAIL:
                if update_contact(vault, display, email, dt, link_text):
                    counts["contacts_created"] += 1

            # Thread (always)
            if update_thread(vault, thread_subject, dt, email, link_text, thread_subject):
                counts["threads_created"] += 1

            # Topic (only for quarantined / categorized buckets)
            if bucket in QUARANTINE_BUCKETS:
                label = BUCKET_TO_LABEL[bucket]
                if update_topic(vault, label, dt, email, link_text, thread_subject):
                    counts["topics_created"] += 1

    # Update state
    if not args.dry_run:
        merged_seen = list(dict.fromkeys((state.get("seen_message_ids", []) + new_seen)))[-2000:]
        state["seen_message_ids"] = merged_seen
        state["last_sync_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        save_state(state_path, state)

    # Summary
    summary = {**counts, "last_sync_at": state.get("last_sync_at")}
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
