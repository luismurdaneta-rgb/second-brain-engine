#!/usr/bin/env python3
"""
calendar_sync.py — incremental Google-Calendar-to-Obsidian-vault sync.

Like gmail_sync.py, this script does NOT call the Calendar MCP itself.
The orchestrator (a scheduled task) fetches events and feeds them as JSON.

Output layout (under Vaults/Google Data/):

    Calendar/<YYYY>/<MM-Mon>/<YYYY-MM-DD HHMM event-summary>.md
    Events/<series-slug>.md            — one MOC per recurring series
    Contacts/<Name>.md                 — incrementally appended (attendee back-links)

Idempotent: re-running with the same JSON updates events that changed
(uses the event's `updated` timestamp) and skips unchanged ones.

JSON input shape:
{
  "events": [
    {
      "calendar_id": "you@example.com",
      "calendar_name": "you@example.com",
      "event": <event JSON from list_events / get_event, verbatim>
    },
    ...
  ]
}

State file:
{
  "last_sync_at": "2026-05-09T16:00:00Z",
  "seen_event_versions": { "<event_id>": "<event.updated ISO>" }
}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone, timedelta
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

# ── Utilities ──────────────────────────────────────────────────────────────────

INVALID_PATH_CHARS = re.compile(r'[\\/:*?"<>|]')
WS = re.compile(r"\s+")


def safe_filename(s: str, maxlen: int = 100) -> str:
    s = INVALID_PATH_CHARS.sub("_", s or "")
    s = WS.sub(" ", s).strip()
    return s[:maxlen] or "untitled"


def slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s or "", flags=re.UNICODE).strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:80] or "no-summary"


def yaml_escape(s) -> str:
    if s is None:
        return '""'
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def yaml_list(items: list) -> str:
    if not items:
        return "[]"
    return "\n  - " + "\n  - ".join(yaml_escape(i) for i in items)


def parse_dt(value: dict | str) -> Optional[datetime]:
    """Parse an event's start/end node (dict with dateTime|date) or ISO string."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value
    elif isinstance(value, dict):
        s = value.get("dateTime") or value.get("date")
        if not s:
            return None
        if "T" not in s:
            # all-day date (YYYY-MM-DD); treat as midnight local
            s = s + "T00:00:00"
    else:
        return None
    try:
        if s.endswith("Z"):
            return datetime.fromisoformat(s[:-1]).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(s)
    except Exception:
        return None


# Email → preferred display name map. Add an entry here whenever WhatsApp /
# other sources know a person by a friendlier name than the email-derived stem.
EMAIL_DISPLAY_OVERRIDES: dict[str, str] = {
    "forneck.naiara@gmail.com": "Naiara Forneck",
    "daniel_v17@hotmail.com": "Daniel Veludo",
    "barbarambernardo@gmail.com": "Barbara Bernardo",
    "rlimaarq@gmail.com": "Rogério Maicpt",
}


def email_to_display(email: str) -> str:
    """Override-aware display name for an email."""
    if not email or "@" not in email:
        return email or "Unknown"
    e = email.lower()
    if e in EMAIL_DISPLAY_OVERRIDES:
        return EMAIL_DISPLAY_OVERRIDES[e]
    local = email.split("@", 1)[0]
    return local.replace(".", " ").replace("_", " ").strip()


# ── Note builders ──────────────────────────────────────────────────────────────


def event_filename(event: dict, start_dt: datetime) -> str:
    summary = event.get("summary") or "(no title)"
    base = f"{start_dt.strftime('%Y-%m-%d %H%M')} {summary}"
    return safe_filename(base) + ".md"


def event_path(vault: Path, event: dict, start_dt: datetime) -> Path:
    return (vault / "Calendar" / str(start_dt.year)
            / MONTH_DIR[start_dt.month] / event_filename(event, start_dt))


def _attendee_display(vault: Path, email: str) -> str:
    """Return existing Contact-note stem if found, else email-derived display."""
    p = _find_existing_contact(vault, email)
    if p:
        return p.stem
    return email_to_display(email)


def build_event_note(vault: Path, event: dict, calendar_name: str, start_dt: datetime, end_dt: Optional[datetime]) -> str:
    summary = event.get("summary") or "(no title)"
    location = event.get("location") or ""
    description = event.get("description") or ""
    attendees = event.get("attendees") or []
    attendee_emails = [a.get("email") for a in attendees if a.get("email")]
    organizer = (event.get("organizer") or {}).get("email", "")
    creator = (event.get("creator") or {}).get("email", "")
    conf = event.get("conferenceUrl") or ""
    html_link = event.get("htmlLink") or ""
    status = event.get("status") or ""
    recurring_id = event.get("recurringEventId") or ""

    # find my response if I'm in attendees
    my_response = ""
    for a in attendees:
        if a.get("self") or (a.get("email") == ME_EMAIL):
            my_response = a.get("responseStatus") or ""
            break

    duration_min = ""
    if end_dt and start_dt:
        try:
            duration_min = str(int((end_dt - start_dt).total_seconds() // 60))
        except Exception:
            duration_min = ""

    front = [
        "---",
        "type: event",
        f"date: {start_dt.strftime('%Y-%m-%d')}",
        f'time: "{start_dt.strftime("%H:%M")}"',
        f'end_time: "{end_dt.strftime("%H:%M") if end_dt else ""}"',
        f"duration_minutes: {duration_min}",
        f"calendar: {yaml_escape(calendar_name)}",
        f"summary: {yaml_escape(summary)}",
        f"location: {yaml_escape(location)}",
        f"attendees:{yaml_list(attendee_emails)}",
        f"organizer: {yaml_escape(organizer)}",
        f"creator: {yaml_escape(creator)}",
        f"status: {yaml_escape(status)}",
        f"my_response: {yaml_escape(my_response)}",
        f"event_id: {yaml_escape(event.get('id', ''))}",
        f"recurring_event_id: {yaml_escape(recurring_id)}",
        f"conference_url: {yaml_escape(conf)}",
        f"html_link: {yaml_escape(html_link)}",
        f"created: {yaml_escape(event.get('created', ''))}",
        f"updated: {yaml_escape(event.get('updated', ''))}",
        "type_tag: event",
        "---",
        "",
    ]

    # Body
    body = [f"# {summary}", ""]
    when = start_dt.strftime("%A, %Y-%m-%d %H:%M")
    if end_dt:
        when = f"{when} – {end_dt.strftime('%H:%M')}"
    body.append(f"**When:** {when}")
    if location:
        body.append(f"**Where:** {location}")
    if conf:
        body.append(f"**Conference:** {conf}")
    body.append(f"**Calendar:** {calendar_name}")
    body.append(f"**Status:** {status}")
    if my_response:
        body.append(f"**My RSVP:** {my_response}")
    body.append("")

    if attendees:
        body.append("## Attendees")
        body.append("")
        for a in attendees:
            email = a.get("email", "")
            rs = a.get("responseStatus", "")
            self_marker = " (me)" if a.get("self") or email == ME_EMAIL else ""
            body.append(f"- {email}{self_marker} — {rs}")
        body.append("")

    if description:
        body.append("## Description")
        body.append("")
        body.append(description.strip())
        body.append("")

    # Connections block
    body.append("<!-- connections:start -->")
    body.append("## Connections")
    body.append("")
    for email in attendee_emails:
        if email == ME_EMAIL:
            body.append(f"- **Me:** [[{ME_DISPLAY}|me]]")
        else:
            body.append(f"- **Attendee:** [[{_attendee_display(vault, email)}]]")
    body.append(f"- **Calendar:** [[{calendar_name}]]")
    if recurring_id:
        # link to the series MOC
        series_slug = slugify(summary)
        body.append(f"- **Series:** [[{series_slug}]]")
    body.append("<!-- connections:end -->")
    body.append("")

    return "\n".join(front + body)


# ── MOC updaters (Contacts, Events series) ─────────────────────────────────────


_contact_index_cache: dict[str, Path] | None = None


def _find_existing_contact(vault: Path, email: str) -> Optional[Path]:
    """Scan Contacts/ once per run, find a note whose `email:` frontmatter matches."""
    global _contact_index_cache
    if _contact_index_cache is None:
        _contact_index_cache = {}
        contacts_dir = vault / "Contacts"
        if contacts_dir.exists():
            email_re = re.compile(r"^email:\s*(.+?)\s*$", re.MULTILINE)
            for f in contacts_dir.glob("*.md"):
                try:
                    head = f.read_text(encoding="utf-8", errors="ignore")[:2000]
                    m = email_re.search(head)
                    if m:
                        _contact_index_cache[m.group(1).strip().lower()] = f
                except Exception:
                    continue
    return _contact_index_cache.get(email.lower())


def update_contact_for_event(vault: Path, email: str, start_dt: datetime, link_text: str, summary: str) -> bool:
    """Append/create a Contact note for an event attendee."""
    if email == ME_EMAIL:
        return False
    # Prefer existing contact note keyed by email (so Gmail's "Tatiana Pedrosa" wins
    # over the email-derived display name).
    existing = _find_existing_contact(vault, email)
    if existing:
        p = existing
        display = p.stem
    else:
        display = email_to_display(email)
        p = vault / "Contacts" / f"{safe_filename(display)}.md"
        # cache the new path so subsequent attendees in the same run reuse it
        if _contact_index_cache is not None:
            _contact_index_cache[email.lower()] = p
    year = str(start_dt.year)
    date_str = start_dt.strftime("%Y-%m-%d")
    line = f"- {date_str} 📅 [[{link_text}|{summary[:60]}]]"

    if not p.exists():
        domain = email.split("@", 1)[1] if "@" in email else ""
        contents = (
            "---\n"
            "type: contact\n"
            f"name: {yaml_escape(display)}\n"
            f"email: {email}\n"
            f"first_email: {date_str}\n"
            f"last_email: {date_str}\n"
            "email_count: 0\n"
            "event_count: 1\n"
            f"domain: {domain}\n"
            "tags:\n"
            "  - contact\n"
            "---\n\n"
            f"# {display}\n\n"
            f"**Email:** {email}\n\n"
            f"## Calendar events\n\n"
            f"### {year}\n\n"
            f"{line}\n"
        )
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(contents, encoding="utf-8")
        return True

    text = p.read_text(encoding="utf-8")
    if line in text:
        return False

    # Ensure ## Calendar events section exists
    if "## Calendar events" not in text:
        text = text.rstrip() + f"\n\n## Calendar events\n\n### {year}\n\n{line}\n"
    else:
        # Find/insert under "### <year>" within the Calendar events section
        cal_idx = text.index("## Calendar events")
        next_section = text.find("\n## ", cal_idx + 1)
        section = text[cal_idx:next_section] if next_section != -1 else text[cal_idx:]
        rest_before = text[:cal_idx]
        rest_after = text[next_section:] if next_section != -1 else ""

        year_heading = f"### {year}"
        if year_heading in section:
            yi = section.index(year_heading)
            yn = section.find("\n### ", yi + len(year_heading))
            if yn == -1:
                section = section.rstrip() + "\n" + line + "\n"
            else:
                section = section[:yn].rstrip() + "\n" + line + "\n\n" + section[yn:].lstrip()
        else:
            section = section.rstrip() + f"\n\n{year_heading}\n\n{line}\n"

        text = rest_before + section + ("\n" if rest_after and not section.endswith("\n") else "") + rest_after

    # bump event_count
    if "event_count:" in text:
        text = re.sub(r"^event_count:\s*(\d+)\s*$",
                      lambda m: f"event_count: {int(m.group(1)) + 1}",
                      text, count=1, flags=re.MULTILINE)
    else:
        # inject between email_count and tags
        text = re.sub(r"^(email_count:.*)$", r"\1\nevent_count: 1",
                      text, count=1, flags=re.MULTILINE)

    p.write_text(text, encoding="utf-8")
    return False


def update_event_series(vault: Path, summary: str, start_dt: datetime, attendees: list[str], link_text: str) -> bool:
    """Maintain an Events/<slug>.md MOC for each recurring series."""
    slug = slugify(summary)
    p = vault / "Events" / f"{slug}.md"
    date_str = start_dt.strftime("%Y-%m-%d %H:%M")
    line = f"- {date_str} — [[{link_text}|{summary[:60]}]]"

    if not p.exists():
        contents = (
            "---\n"
            "type: event-series\n"
            f"summary: {yaml_escape(summary)}\n"
            f"first_occurrence: {start_dt.strftime('%Y-%m-%d')}\n"
            f"last_occurrence: {start_dt.strftime('%Y-%m-%d')}\n"
            "occurrence_count: 1\n"
            "tags:\n"
            "  - event-series\n"
            "---\n\n"
            f"# {summary}\n\n"
            f"**Occurrences:** 1\n"
            f"**First:** {start_dt.strftime('%Y-%m-%d')}\n\n"
            "## Participants\n\n"
            + "\n".join(f"- {a}" for a in attendees if a)
            + "\n\n## Occurrences\n\n"
            + line + "\n"
        )
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(contents, encoding="utf-8")
        return True

    text = p.read_text(encoding="utf-8")
    if link_text in text:
        return False

    if "## Occurrences" in text:
        text = text.rstrip() + "\n" + line + "\n"
    else:
        text = text.rstrip() + "\n\n## Occurrences\n\n" + line + "\n"

    text = re.sub(r"^occurrence_count:\s*(\d+)\s*$",
                  lambda m: f"occurrence_count: {int(m.group(1)) + 1}",
                  text, count=1, flags=re.MULTILINE)
    text = re.sub(r"^last_occurrence:.*$",
                  f"last_occurrence: {start_dt.strftime('%Y-%m-%d')}",
                  text, count=1, flags=re.MULTILINE)
    text = re.sub(r"\*\*Occurrences:\*\*\s*\d+",
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
    return {"last_sync_at": None, "seen_event_versions": {}}


def save_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True, help="JSON file: {events: [{calendar_id, calendar_name, event}]}")
    ap.add_argument("--vault", required=True, help="Path to Vaults/Google Data/")
    ap.add_argument("--state", required=True, help="Cursor state JSON path")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    events_path = Path(args.events)
    vault = Path(args.vault)
    state_path = Path(args.state)

    if not events_path.exists():
        print(f"ERROR: {events_path} does not exist", file=sys.stderr)
        return 2

    if not vault.exists():
        print(f"ERROR: vault {vault} does not exist", file=sys.stderr)
        return 2

    payload = json.loads(events_path.read_text(encoding="utf-8"))
    state = load_state(state_path)
    seen: dict = state.get("seen_event_versions", {})

    counts = {
        "events_written": 0,
        "events_updated": 0,
        "skipped_unchanged": 0,
        "contacts_touched": 0,
        "series_created": 0,
        "skipped_cancelled": 0,
    }

    items = payload.get("events", [])
    for entry in items:
        event = entry.get("event") or {}
        cal_name = entry.get("calendar_name") or entry.get("calendar_id") or "unknown"
        ev_id = event.get("id", "")
        if not ev_id:
            continue

        # Skip cancelled events outright
        if event.get("status") == "cancelled":
            counts["skipped_cancelled"] += 1
            seen[ev_id] = event.get("updated") or seen.get(ev_id, "")
            continue

        start_dt = parse_dt(event.get("start"))
        if not start_dt:
            continue
        end_dt = parse_dt(event.get("end"))

        updated_at = event.get("updated") or ""
        if seen.get(ev_id) == updated_at and not args.dry_run:
            counts["skipped_unchanged"] += 1
            continue

        out = event_path(vault, event, start_dt)
        is_new = not out.exists()
        if args.dry_run:
            print(f"[dry-run] {'NEW ' if is_new else 'UPD '} {out}")
            seen[ev_id] = updated_at
            continue

        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(build_event_note(vault, event, cal_name, start_dt, end_dt), encoding="utf-8")
        if is_new:
            counts["events_written"] += 1
        else:
            counts["events_updated"] += 1

        link_text = out.stem
        attendee_emails = [
            a.get("email") for a in (event.get("attendees") or []) if a.get("email")
        ]
        for email in attendee_emails:
            if email == ME_EMAIL:
                continue
            update_contact_for_event(
                vault, email, start_dt, link_text,
                event.get("summary") or "(no title)"
            )
            counts["contacts_touched"] += 1

        if event.get("recurringEventId"):
            if update_event_series(vault, event.get("summary") or "(no title)",
                                   start_dt, attendee_emails, link_text):
                counts["series_created"] += 1

        seen[ev_id] = updated_at

    if not args.dry_run:
        # Cap state size
        state["seen_event_versions"] = dict(list(seen.items())[-5000:])
        state["last_sync_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        save_state(state_path, state)

    summary = {**counts, "last_sync_at": state.get("last_sync_at")}
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
