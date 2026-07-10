#!/usr/bin/env python3
"""archive_conversation.py — archive a Cowork/Claude conversation to Vaults/Claude/.

Usage:
    # Write a transcript as a new conversation note (idempotent by session_id).
    python3 archive_conversation.py write \\
        --session-id <uuid> \\
        --created YYYY-MM-DDTHH:MM:SS \\
        --last-message YYYY-MM-DDTHH:MM:SS \\
        --model claude-opus-4-7 \\
        --message-count 38 \\
        --tool-call-count 152 \\
        --transcript-file /tmp/transcript.md \\
        --title "Building the Second Brain skills" \\
        [--source cowork|claude-web]

    # Check if a session is already archived
    python3 archive_conversation.py exists --session-id <uuid>

    # List archived sessions
    python3 archive_conversation.py list
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import os

import lib_vault

DEFAULT_BASE_MAC = lib_vault.HOST_BASE
DEFAULT_BASE_SANDBOX = Path("/sessions/cool-wonderful-archimedes/mnt/Second Brain")

CLAUDE_VAULT_REL = Path("Vaults") / "Claude"
INBOX_REL = CLAUDE_VAULT_REL / "0_Inbox"

FM_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def _safe_exists(p: Path) -> bool:
    try:
        return p.exists()
    except (PermissionError, OSError):
        return False


def base_dir() -> Path:
    env = os.environ.get("SECOND_BRAIN_BASE")
    if env and _safe_exists(Path(env)):
        return Path(env)
    if _safe_exists(DEFAULT_BASE_MAC):
        return DEFAULT_BASE_MAC
    if _safe_exists(DEFAULT_BASE_SANDBOX):
        return DEFAULT_BASE_SANDBOX
    # Auto-discover any /sessions/<name>/mnt/Second Brain mount
    sessions_root = Path("/sessions")
    if _safe_exists(sessions_root):
        for entry in sessions_root.iterdir():
            cand = entry / "mnt" / "Second Brain"
            if _safe_exists(cand):
                return cand
    raise SystemExit("Cannot find Second Brain root.")


def vault_dir() -> Path:
    return base_dir() / CLAUDE_VAULT_REL


def safe_filename(name: str, max_len: int = 100) -> str:
    s = re.sub(r'[/\\:*?"<>|]', "-", name)
    s = re.sub(r"\s+", " ", s).replace("\n", " ").strip().strip(".")
    return (s or "Untitled")[:max_len]


def find_existing_session(session_id: str) -> Path | None:
    """Search Vaults/Claude/ for a note with matching session_id frontmatter.
    Returns the path if found, else None."""
    if not session_id:
        return None
    v = vault_dir()
    if not v.exists():
        return None
    needle = f"session_id: {session_id}"
    needle_q = f'session_id: "{session_id}"'
    for md in v.rglob("*.md"):
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        # Only check frontmatter portion to be fast
        m = FM_RE.match(text)
        fm = m.group(1) if m else text[:1000]
        if needle in fm or needle_q in fm:
            return md
    return None


def daily_note_stem(created: str) -> str:
    """Return the date stem like '2026-05-09' from an ISO created string."""
    try:
        return created[:10]
    except Exception:
        return ""


def render_note(args, transcript_body: str) -> str:
    """Build the full markdown including frontmatter, header, transcript, connections."""
    title = args.title or "Untitled conversation"
    daily_stem = daily_note_stem(args.created)

    fm = []
    fm.append("---")
    fm.append("type: claude-conversation")
    fm.append(f"source: {args.source}")
    fm.append(f"session_id: {args.session_id}")
    fm.append(f'title: "{title.replace(chr(34), chr(39))}"')
    fm.append(f"created: {args.created}")
    fm.append(f"last_message: {args.last_message}")
    fm.append(f"model: {args.model}")
    fm.append(f"message_count: {args.message_count}")
    fm.append(f"tool_call_count: {args.tool_call_count}")
    fm.append("para: inbox")
    fm.append('topic: ""')
    fm.append("tags:")
    fm.append("  - claude-conversation")
    fm.append(f"  - {args.source}")
    fm.append("---")
    fm.append("")

    body = []
    body.append(f"# {title}")
    body.append("")
    body.append(transcript_body.rstrip())
    body.append("")
    body.append("<!-- connections:start -->")
    body.append("## Connections")
    body.append("")
    if daily_stem:
        body.append(f"- **Daily note:** [[{daily_stem}]]")
    body.append("- **Topic:** _(triage from `0_Inbox/` and fill in)_")
    body.append("<!-- connections:end -->")

    return "\n".join(fm) + "\n".join(body) + "\n"


def cmd_write(args):
    existing = find_existing_session(args.session_id)
    if existing:
        print(f"Session {args.session_id} already archived at {existing}; skipping.")
        return 0

    transcript = Path(args.transcript_file).read_text(encoding="utf-8")
    text = render_note(args, transcript)

    # Choose filename
    base = safe_filename(args.title or f"Session {args.session_id[:8]}")
    inbox = base_dir() / INBOX_REL
    inbox.mkdir(parents=True, exist_ok=True)
    target = inbox / f"{base}.md"
    if target.exists():
        # Title collision with a different session — disambiguate by session prefix
        target = inbox / f"{base} ({args.session_id[:8]}).md"
    target.write_text(text, encoding="utf-8")
    print(f"Archived to {target}")
    return 0


def cmd_exists(args):
    existing = find_existing_session(args.session_id)
    if existing:
        print(f"yes: {existing}")
        return 0
    print("no")
    return 1


def cmd_list(args):
    v = vault_dir()
    if not v.exists():
        print("Claude vault does not exist yet")
        return 0
    rows = []
    for md in v.rglob("*.md"):
        if md.name.startswith("_"):
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        m = FM_RE.match(text)
        if not m:
            continue
        fm = m.group(1)
        sid = re.search(r'^session_id:\s*([^\n]+)', fm, re.M)
        title = re.search(r'^title:\s*"?([^"\n]+?)"?\s*$', fm, re.M)
        created = re.search(r'^created:\s*([^\n]+)', fm, re.M)
        if sid:
            rows.append((
                created.group(1) if created else "?",
                sid.group(1).strip(),
                title.group(1).strip() if title else md.stem,
                md.relative_to(v),
            ))
    rows.sort()
    print(f"{len(rows)} archived sessions:")
    for created, sid, title, p in rows:
        print(f"  {created}  {sid[:8]}…  {title[:60]}  ({p})")


def main():
    ap = argparse.ArgumentParser(description="Archive Claude conversations to Vaults/Claude/")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_w = sub.add_parser("write", help="Archive a conversation")
    p_w.add_argument("--session-id", required=True)
    p_w.add_argument("--created", required=True, help="ISO timestamp")
    p_w.add_argument("--last-message", required=True, help="ISO timestamp")
    p_w.add_argument("--model", required=True)
    p_w.add_argument("--message-count", type=int, default=0)
    p_w.add_argument("--tool-call-count", type=int, default=0)
    p_w.add_argument("--title", required=True)
    p_w.add_argument("--transcript-file", required=True)
    p_w.add_argument("--source", default="cowork", choices=["cowork", "claude-web"])
    p_w.set_defaults(func=cmd_write)

    p_e = sub.add_parser("exists", help="Check whether a session is already archived")
    p_e.add_argument("--session-id", required=True)
    p_e.set_defaults(func=cmd_exists)

    p_l = sub.add_parser("list", help="List archived sessions")
    p_l.set_defaults(func=cmd_list)

    args = ap.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
