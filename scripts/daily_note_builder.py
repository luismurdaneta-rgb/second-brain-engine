#!/usr/bin/env python3
"""daily_note_builder.py — write or append today's daily note in Vaults/Daily/.

Usage:
    # Write or append today's content (content-file is a markdown fragment
    # with the four sections; see SKILL.md for shape).
    python3 daily_note_builder.py write --date 2026-05-09 --content-file /tmp/today.md

    # Print the path of today's note (no write)
    python3 daily_note_builder.py path --date 2026-05-09

    # Read content from stdin
    cat content.md | python3 daily_note_builder.py write --date 2026-05-09 --stdin

The content file should provide sections labeled `## Summary`, `## Files & vaults touched`,
`## Entities mentioned`, `## Ideas & open questions`, `## Sessions` (any subset is OK).
The script merges them into the master daily file: first write creates the full template,
subsequent writes append "Update at HH:MM" sub-sections.
"""
from __future__ import annotations
import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

import os

import lib_vault

DEFAULT_BASE_MAC = lib_vault.HOST_BASE
DEFAULT_BASE_SANDBOX = Path("/sessions/cool-wonderful-archimedes/mnt/Second Brain")

SECTIONS = [
    ("Summary", "## Summary"),
    ("Files & vaults touched", "## Files & vaults touched"),
    ("Entities mentioned", "## Entities mentioned"),
    ("Ideas & open questions", "## Ideas & open questions"),
    ("Sessions", "## Sessions"),
]
SESSION_S = "<!-- session-block:start -->"
SESSION_E = "<!-- session-block:end -->"


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
    sessions_root = Path("/sessions")
    if _safe_exists(sessions_root):
        for entry in sessions_root.iterdir():
            cand = entry / "mnt" / "Second Brain"
            if _safe_exists(cand):
                return cand
    raise SystemExit("Cannot find Second Brain root.")


def daily_path(date_str: str) -> Path:
    """Compute the daily note path for a YYYY-MM-DD date string."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return base_dir() / "Vaults" / "Daily" / dt.strftime("%Y") / dt.strftime("%m") / f"{date_str}.md"


def parse_sections(content: str) -> dict:
    """Parse a markdown fragment into {section_label: section_text}."""
    out = {label: "" for label, _ in SECTIONS}
    cur = None
    cur_lines = []
    for line in content.splitlines():
        s = line.strip()
        matched = None
        for label, header in SECTIONS:
            if s == header:
                matched = label
                break
        if matched:
            if cur:
                out[cur] = "\n".join(cur_lines).strip()
            cur = matched
            cur_lines = []
        else:
            if cur:
                cur_lines.append(line)
    if cur:
        out[cur] = "\n".join(cur_lines).strip()
    return out


def initial_template(date_str: str, sections: dict) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    weekday = dt.strftime("%A")
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    lines = []
    lines.append("---")
    lines.append("type: daily")
    lines.append(f"date: {date_str}")
    lines.append("tags:")
    lines.append("  - daily")
    lines.append("sessions: 1")
    lines.append(f"last_updated: {now}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {date_str} — {weekday}")
    lines.append("")
    for label, header in SECTIONS:
        lines.append(header)
        lines.append("")
        body = sections.get(label, "").strip()
        if label == "Sessions":
            lines.append(SESSION_S)
            if body:
                lines.append(body)
            lines.append(SESSION_E)
        else:
            lines.append(body if body else "_(none)_")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def append_update(existing: str, new_sections: dict) -> str:
    """Add a new 'Update at HH:MM' subsection under each section that has new content.
    Sessions section: append inside the marker block, deduped by session ID line."""
    now_label = datetime.now().strftime("%H:%M")
    text = existing

    # Update last_updated frontmatter
    now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    text = re.sub(r"^last_updated:.*$", f"last_updated: {now_iso}", text, count=1, flags=re.MULTILINE)

    # Increment session count
    def inc_sessions(m):
        try:
            n = int(m.group(1)) + 1
        except ValueError:
            n = 1
        return f"sessions: {n}"
    text = re.sub(r"^sessions:\s*(\d+)", inc_sessions, text, count=1, flags=re.MULTILINE)

    # For each non-Session section, append an "Update at HH:MM" subsection if there's content
    for label, header in SECTIONS[:-1]:  # all except Sessions
        body = (new_sections.get(label) or "").strip()
        if not body:
            continue
        update_block = f"\n### Update at {now_label}\n\n{body}\n"
        # Insert right after the section header and any blank line
        pattern = re.compile(rf"^{re.escape(header)}\s*\n", re.MULTILINE)
        m = pattern.search(text)
        if not m:
            # Section missing entirely — append at end
            text = text.rstrip() + f"\n\n{header}\n{update_block}\n"
            continue
        # Find next "## " heading (or end of file) to bound this section
        section_start = m.end()
        next_h = re.search(r"^## ", text[section_start:], re.MULTILINE)
        section_end = section_start + next_h.start() if next_h else len(text)
        # If the section currently contains "_(none)_", remove that placeholder
        section_body = text[section_start:section_end]
        if section_body.strip() in ("_(none)_", ""):
            new_body = update_block.lstrip() + "\n"
            text = text[:section_start] + new_body + text[section_end:]
        else:
            text = text[:section_end].rstrip() + "\n" + update_block + "\n" + text[section_end:]

    # Sessions: append inside marker block, dedup by session header lines
    sess_body = (new_sections.get("Sessions") or "").strip()
    if sess_body:
        s_idx = text.find(SESSION_S)
        e_idx = text.find(SESSION_E, s_idx) if s_idx >= 0 else -1
        if s_idx < 0 or e_idx < 0:
            # Marker missing — append a fresh block at end of Sessions section
            sess_header_match = re.search(r"^## Sessions\s*$", text, re.MULTILINE)
            if sess_header_match:
                insert_at = sess_header_match.end()
                next_h = re.search(r"^## ", text[insert_at:], re.MULTILINE)
                insert_at = insert_at + next_h.start() if next_h else len(text)
                fresh = f"\n{SESSION_S}\n{sess_body}\n{SESSION_E}\n"
                text = text[:insert_at].rstrip() + fresh + text[insert_at:]
            else:
                # No Sessions section at all — e.g. note seeded by morning_brief.py,
                # which writes only frontmatter + H1 + the brief block. Create the
                # whole section with markers at end of file so sessions aren't dropped.
                fresh = f"\n\n## Sessions\n\n{SESSION_S}\n{sess_body}\n{SESSION_E}\n"
                text = text.rstrip() + fresh
        else:
            existing_block = text[s_idx + len(SESSION_S):e_idx]
            # Dedup by session ID lines (lines starting with "### Session ")
            existing_ids = set(re.findall(r"^### Session ([^\n—]+?)(?:\s+—|\s*$)", existing_block, re.MULTILINE))
            new_lines = []
            skip = False
            for line in sess_body.splitlines():
                m = re.match(r"^### Session ([^\n—]+?)(?:\s+—|\s*$)", line)
                if m:
                    skip = m.group(1).strip() in existing_ids
                if not skip:
                    new_lines.append(line)
            new_block_addition = "\n".join(new_lines).strip()
            if new_block_addition:
                merged = existing_block.rstrip() + "\n\n" + new_block_addition + "\n"
                text = text[:s_idx + len(SESSION_S)] + "\n" + merged.lstrip() + text[e_idx:]

    return text


def cmd_path(args):
    print(daily_path(args.date))


def cmd_write(args):
    if args.stdin:
        content = sys.stdin.read()
    else:
        if not args.content_file:
            raise SystemExit("Provide --content-file or --stdin")
        content = Path(args.content_file).read_text(encoding="utf-8")
    sections = parse_sections(content)
    target = daily_path(args.date)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(initial_template(args.date, sections), encoding="utf-8")
        print(f"Created {target}")
    else:
        existing = target.read_text(encoding="utf-8")
        new_text = append_update(existing, sections)
        target.write_text(new_text, encoding="utf-8")
        print(f"Appended to {target}")


def cmd_list_sessions(args):
    print("This subcommand is a placeholder; from a Claude session use mcp__session_info__list_sessions directly.")


def main():
    ap = argparse.ArgumentParser(description="Build daily notes for the Second Brain Daily vault")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_path = sub.add_parser("path", help="Print today's note path")
    p_path.add_argument("--date", required=True, help="YYYY-MM-DD")
    p_path.set_defaults(func=cmd_path)

    p_write = sub.add_parser("write", help="Write or append today's note")
    p_write.add_argument("--date", required=True, help="YYYY-MM-DD")
    p_write.add_argument("--content-file", help="Path to staged markdown fragment")
    p_write.add_argument("--stdin", action="store_true", help="Read content from stdin")
    p_write.set_defaults(func=cmd_write)

    p_ls = sub.add_parser("list-sessions", help="(placeholder) list cowork sessions visible to MCP")
    p_ls.set_defaults(func=cmd_list_sessions)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
