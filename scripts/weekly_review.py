#!/usr/bin/env python3
"""weekly_review.py — produce a weekly digest from Vaults/Daily/<YYYY>/<MM>/*.md.

Reads daily notes for an ISO week, aggregates themes/files/entities/ideas,
writes Vaults/Daily/Weekly/<YYYY>-W<NN>.md.

Usage:
    python3 weekly_review.py current               # this week (ISO)
    python3 weekly_review.py week 2026-W19         # specific ISO week
    python3 weekly_review.py path 2026-W19         # just print target path
    python3 weekly_review.py week 2026-W19 --no-write   # dry run
"""
from __future__ import annotations
import argparse
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import lib_vault

DEFAULT_BASE_MAC = lib_vault.HOST_BASE


def base_dir() -> Path:
    import os
    env = os.environ.get("SECOND_BRAIN_ROOT")
    if env:
        p = Path(env)
        if p.exists():
            return p
    try:
        if DEFAULT_BASE_MAC.exists():
            return DEFAULT_BASE_MAC
    except PermissionError:
        pass
    # Try any current sandbox mount (session ID changes between runs)
    try:
        for sess in Path("/sessions").iterdir():
            cand = sess / "mnt" / "Second Brain"
            try:
                if cand.exists():
                    return cand
            except PermissionError:
                continue
    except (FileNotFoundError, PermissionError):
        pass
    raise SystemExit("Cannot find Second Brain root.")


def daily_dir() -> Path:
    return base_dir() / "Vaults" / "Daily"


def weekly_dir() -> Path:
    d = daily_dir() / "Weekly"
    d.mkdir(parents=True, exist_ok=True)
    return d


def parse_iso_week(s: str) -> tuple[int, int]:
    m = re.match(r"^(\d{4})-W(\d{1,2})$", s)
    if not m:
        raise SystemExit(f"Bad ISO week: {s!r}; expected like 2026-W19")
    return int(m.group(1)), int(m.group(2))


def week_range(year: int, week: int) -> tuple[date, date]:
    """Return Monday→Sunday of an ISO week."""
    monday = date.fromisocalendar(year, week, 1)
    sunday = monday + timedelta(days=6)
    return monday, sunday


def current_iso_week() -> tuple[int, int]:
    today = date.today()
    iso = today.isocalendar()
    return iso.year, iso.week


def daily_path_for(d: date) -> Path:
    return daily_dir() / f"{d.year}" / f"{d.month:02d}" / f"{d.isoformat()}.md"


def read_section(text: str, header: str) -> str:
    """Return the body under '## <header>' up to the next '## ' or end."""
    pat = re.compile(rf"^## {re.escape(header)}\s*$", re.MULTILINE)
    m = pat.search(text)
    if not m:
        return ""
    start = m.end()
    nxt = re.search(r"^## ", text[start:], re.MULTILINE)
    end = start + nxt.start() if nxt else len(text)
    return text[start:end].strip()


def parse_daily(text: str) -> dict:
    """Extract structured pieces from a daily note."""
    return {
        "summary": read_section(text, "Summary"),
        "files": read_section(text, "Files & vaults touched"),
        "entities": read_section(text, "Entities mentioned"),
        "ideas": read_section(text, "Ideas & open questions"),
    }


def collect_wikilinks(text: str) -> list[str]:
    return re.findall(r"\[\[([^\]\|]+)(?:\|[^\]]*)?\]\]", text or "")


def collect_bullets(text: str) -> list[str]:
    """Return individual bullet items, stripped, from a markdown body."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith(("- ", "* ")):
            item = line[2:].strip()
            if item:
                out.append(item)
    return out


def first_sentence(s: str, max_chars: int = 120) -> str:
    s = (s or "").replace("\n", " ").strip()
    if not s:
        return ""
    m = re.search(r"(?<=[.!?])\s", s)
    cut = m.start() if m else len(s)
    s = s[:cut].strip()
    return s if len(s) <= max_chars else s[: max_chars - 1] + "…"


def aggregate(year: int, week: int) -> dict:
    monday, sunday = week_range(year, week)
    days = []
    parsed = {}
    for i in range(7):
        d = monday + timedelta(days=i)
        p = daily_path_for(d)
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="replace")
            parsed[d] = parse_daily(text)
            days.append(d)

    # Themes: just deliver each day's headline; user/agent narrows during render.
    headlines = {d: first_sentence(parsed[d]["summary"]) for d in days}

    # Files: bullets across all files sections; rank by frequency
    files_bullets = []
    for d in days:
        for b in collect_bullets(parsed[d]["files"]):
            files_bullets.append((d, b))

    # Entities: collect all wikilinks from entities section, count freq
    entity_counter = Counter()
    entities_per_day = defaultdict(set)
    for d in days:
        for link in collect_wikilinks(parsed[d]["entities"]):
            entity_counter[link] += 1
            entities_per_day[link].add(d)

    # Ideas: list with day attribution, mark recurrence
    idea_to_days = defaultdict(set)
    idea_canonical = {}
    for d in days:
        for b in collect_bullets(parsed[d]["ideas"]):
            key = re.sub(r"\W+", "", b.lower())[:60]
            if not key:
                continue
            idea_canonical.setdefault(key, b)
            idea_to_days[key].add(d)

    recurring = [(idea_canonical[k], sorted(v)) for k, v in idea_to_days.items() if len(v) >= 2]
    once_only = [(idea_canonical[k], sorted(v)[0]) for k, v in idea_to_days.items() if len(v) == 1]

    return {
        "monday": monday,
        "sunday": sunday,
        "days_with_notes": days,
        "headlines": headlines,
        "files_bullets": files_bullets,
        "entity_counter": entity_counter,
        "entities_per_day": entities_per_day,
        "recurring_ideas": recurring,
        "once_ideas": once_only,
    }


def render(year: int, week: int, agg: dict) -> str:
    monday = agg["monday"]
    sunday = agg["sunday"]
    days = agg["days_with_notes"]
    headlines = agg["headlines"]
    entity_counter = agg["entity_counter"]
    recurring = agg["recurring_ideas"]
    once = agg["once_ideas"]

    # Top entities by frequency, capped at 12
    top_entities = entity_counter.most_common(12)

    # Top files: count bullet repeats across the week
    file_paths = Counter()
    for _, b in agg["files_bullets"]:
        # extract a wikilink or computer:// or quoted path as the key
        m = re.search(r"\[\[([^\]\|]+)(?:\|[^\]]*)?\]\]", b)
        if m:
            file_paths[f"[[{m.group(1)}]]"] += 1
            continue
        m = re.search(r"computer://([^)\s]+)", b)
        if m:
            file_paths[f"computer://{m.group(1)}"] += 1
            continue
        m = re.search(r"`([^`]+)`", b)
        if m:
            file_paths[f"`{m.group(1)}`"] += 1
            continue

    week_str = f"{year}-W{week:02d}"
    pretty_range = f"{monday.strftime('%b %-d')} to {sunday.strftime('%b %-d')}"
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    lines = []
    lines.append("---")
    lines.append("type: weekly-review")
    lines.append(f"week: {week_str}")
    lines.append(f"start_date: {monday.isoformat()}")
    lines.append(f"end_date: {sunday.isoformat()}")
    lines.append(f"days_with_notes: {len(days)}")
    lines.append(f"last_updated: {now}")
    lines.append("tags:")
    lines.append("  - weekly")
    lines.append("  - moc")
    lines.append("---")
    lines.append("")
    lines.append(f"# Week {week} of {year} — {pretty_range}")
    lines.append("")

    # Headline (the agent will edit this; default = first day's headline)
    if headlines:
        first_day = days[0]
        lines.append("## Headline")
        lines.append("")
        lines.append(f"_{first_sentence(headlines[first_day], 200) or 'No daily headline available.'}_")
        lines.append("")
        lines.append("> _(Edit this to capture the week's dominant theme. The CEO drafts; you refine.)_")
        lines.append("")

    # Daily notes list
    lines.append("## Daily notes")
    lines.append("")
    for i in range(7):
        d = monday + timedelta(days=i)
        if d in headlines:
            lines.append(f"- [[{d.isoformat()}]] — {first_sentence(headlines[d], 100)}")
        else:
            lines.append(f"- {d.isoformat()} — _(no note)_")
    lines.append("")

    # Themes — placeholder; the agent fills these manually after running
    lines.append("## Themes")
    lines.append("")
    lines.append("_(The CEO drafts 2–4 themes here based on the daily notes above. Edit/refine.)_")
    lines.append("")

    # Files
    if file_paths:
        lines.append("## Files & vaults touched (top by frequency)")
        lines.append("")
        for k, n in file_paths.most_common(12):
            lines.append(f"- {n}× {k}")
        lines.append("")

    # Entities
    if top_entities:
        lines.append("## Entities mentioned (top by frequency)")
        lines.append("")
        for ent, n in top_entities:
            lines.append(f"- {n}× [[{ent}]]")
        lines.append("")

    # Recurring open questions
    lines.append("## Recurring open questions")
    lines.append("")
    if recurring:
        for item, ds in sorted(recurring, key=lambda x: -len(x[1])):
            day_marks = ", ".join(d.strftime("%a %m-%d") for d in ds)
            lines.append(f"- {item} _(appeared on {day_marks})_")
    else:
        lines.append("_(no items appeared in more than one day's Ideas section)_")
    lines.append("")

    # What didn't get done
    lines.append("## What didn't get done")
    lines.append("")
    if once:
        capped = once[:5]
        for item, d in capped:
            lines.append(f"- {item} _(from [[{d.isoformat()}]])_")
        if len(once) > 5:
            lines.append(f"- _…and {len(once) - 5} more single-day items in this week's Ideas sections_")
    else:
        lines.append("_(no unresolved single-day ideas this week)_")
    lines.append("")

    # Looking ahead — left blank for the agent
    lines.append("## Looking ahead")
    lines.append("")
    lines.append("_(The CEO fills this only from explicit 'next' / 'follow up' mentions in the daily notes.)_")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_review(year: int, week: int, content: str) -> Path:
    target = weekly_dir() / f"{year}-W{week:02d}.md"
    if target.exists():
        existing = target.read_text(encoding="utf-8")
        # Append a "## Update at HH:MM" block
        now_label = datetime.now().strftime("%H:%M")
        update = f"\n\n## Update at {now_label}\n\n_(Re-ran weekly review. Latest aggregation:)_\n\n{content}\n"
        target.write_text(existing.rstrip() + update, encoding="utf-8")
        return target
    target.write_text(content, encoding="utf-8")
    return target


def cmd_current(args):
    year, week = current_iso_week()
    return run(year, week, args)


def cmd_week(args):
    year, week = parse_iso_week(args.iso_week)
    return run(year, week, args)


def cmd_path(args):
    year, week = parse_iso_week(args.iso_week)
    print(weekly_dir() / f"{year}-W{week:02d}.md")


def run(year: int, week: int, args) -> int:
    agg = aggregate(year, week)
    content = render(year, week, agg)
    if args.no_write:
        print(content)
        return 0
    target = write_review(year, week, content)
    print(f"Wrote {target}")
    print(f"  Days with notes: {len(agg['days_with_notes'])}")
    print(f"  Top entity: {agg['entity_counter'].most_common(1) or '—'}")
    print(f"  Recurring questions: {len(agg['recurring_ideas'])}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Weekly digest of Second Brain Daily notes")
    ap.add_argument("--no-write", action="store_true", help="Print to stdout instead of writing")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_c = sub.add_parser("current", help="This week (ISO)")
    p_c.set_defaults(func=cmd_current)

    p_w = sub.add_parser("week", help="Specific ISO week")
    p_w.add_argument("iso_week", help="e.g. 2026-W19")
    p_w.set_defaults(func=cmd_week)

    p_p = sub.add_parser("path", help="Print target file path")
    p_p.add_argument("iso_week", help="e.g. 2026-W19")
    p_p.set_defaults(func=cmd_path)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
