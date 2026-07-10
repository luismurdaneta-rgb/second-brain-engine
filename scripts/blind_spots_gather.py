#!/usr/bin/env python3
"""blind_spots_gather.py — assemble the mechanical signals a blind-spots
analysis needs, so the skill (Claude as the orchestrator) doesn't have to
walk 50k notes from scratch.

Outputs a single JSON digest to `_scripts/.blind-spots-digest-<YYYY-MM-DD>.json`.

Signals gathered (all rule-based, no LLM):
  - top_entities          : most-linked [[wikilink]] targets, with/without their own note
  - stated_goals          : intentions parsed from Daily notes + whether they reappear
  - stale_projects        : project notes not modified in N days
  - quiet_contacts        : Contacts/<X>.md notes mentioned often but not recently
  - frustration_phrases   : nearby-context noun phrases co-occurring with frustration words
  - weekly_lookahead      : items from old Weekly review notes — did they execute?
  - dropped_threads       : Threads/ notes where the latest message is from the user with no reply, > N days

Plus a curated `sample_notes` list — paths Claude should read to ground the analysis.

Default depth is FAST (MOCs + recent Daily + project root notes only).
--deep walks the whole vault.

Run:
  python3 blind_spots_gather.py                              # default fast
  python3 blind_spots_gather.py --deep                       # full vault sweep
  python3 blind_spots_gather.py --vaults-root /path --out /tmp/digest.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import lib_vault

DEFAULT_VAULTS_ROOT = lib_vault.vaults_root()
DEFAULT_SCRIPTS_DIR = lib_vault.scripts_dir()

# How far back "recent" goes for the FAST tier
RECENT_DAYS = 90
STALE_PROJECT_DAYS = 120
QUIET_CONTACT_DAYS = 120
DROPPED_GOAL_FOLLOWUP_DAYS = 60

WIKILINK_RE = re.compile(r"\[\[([^|\]\n]+)(?:\|[^\]]+)?\]\]")
TAG_RE = re.compile(r"(?:^|\s)#([A-Za-z][\w/-]+)")
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)

# Markers Claude can rely on to detect stated intentions in daily notes
GOAL_PATTERNS = [
    re.compile(r"(?im)^\s*(?:goal|intent|intention|plan)\s*[:\-]\s*(.+)$"),
    re.compile(r"(?im)^\s*next step[s]?\s*[:\-]\s*(.+)$"),
    re.compile(r"(?im)^\s*todo\s*[:\-]\s*(.+)$"),
    re.compile(r"(?im)^\s*-\s*\[ \]\s+(.+)$"),
    re.compile(r"(?im)\bI(?:'m| am)? going to\s+([^.!?\n]{6,140})"),
    re.compile(r"(?im)\bI want to\s+([^.!?\n]{6,140})"),
    re.compile(r"(?im)\bI need to\s+([^.!?\n]{6,140})"),
    re.compile(r"(?im)\bI should\s+([^.!?\n]{6,140})"),
    re.compile(r"(?im)\bgotta\s+([^.!?\n]{6,140})"),
]

FRUSTRATION_WORDS = (
    "frustrat", "stuck", "blocked", "annoying", "annoyed",
    "hate", "tired of", "fed up", "exhausted", "can't stand",
    "burned out", "drained", "overwhelmed",
)

# Folder name conventions that hold "project root" notes worth sampling fast.
# Covers PARA (1_Projects/2_Areas/3_Resources/4_Archive/0_Inbox), the user's
# graphify layout (communities/, nodes/, Concepts/), and generic _Topics/_Communities.
PROJECT_ROOT_DIR_HINTS = (
    "0_Inbox", "1_Projects", "2_Areas", "3_Resources", "4_Archive",
    "_Projects", "Projects",
    "_Topics", "Topics",
    "_Communities", "Communities",
    "communities", "nodes",
    "Concepts",
)

CONTACTS_DIRS = ("Contacts",)
THREADS_DIRS = ("Threads",)
DAILY_VAULT_NAME = "Daily"

PROTECTED_DIRS = {".obsidian", "_Quarantine", "_archive_chatgpt", "_Sources"}

# ---------- small helpers ----------

def parse_frontmatter(text: str) -> tuple[dict, str]:
    return lib_vault.parse_frontmatter(text)


def safe_read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def days_since(d: dt.date | dt.datetime) -> int:
    if isinstance(d, dt.datetime):
        d = d.date()
    return (dt.date.today() - d).days


def file_mdate(p: Path) -> dt.date:
    return dt.date.fromtimestamp(p.stat().st_mtime)


def is_in_protected(rel: Path) -> bool:
    return bool(set(rel.parts) & PROTECTED_DIRS)


def daily_date_from_name(p: Path) -> dt.date | None:
    """Extract YYYY-MM-DD from a daily-note filename."""
    m = re.search(r"(20\d\d)-(\d\d)-(\d\d)", p.stem)
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


# ---------- gather ----------

@dataclass
class GatherResult:
    generated_at: str
    depth: str
    vaults_scanned: list[str]
    totals: dict[str, Any]
    top_entities: list[dict[str, Any]]
    stated_goals: list[dict[str, Any]]
    stale_projects: list[dict[str, Any]]
    quiet_contacts: list[dict[str, Any]]
    frustration_phrases: list[dict[str, Any]]
    weekly_lookahead: list[dict[str, Any]]
    dropped_threads: list[dict[str, Any]]
    sample_notes: list[str]


def gather(vaults_root: Path, deep: bool) -> GatherResult:
    today = dt.date.today()

    # Discover vaults
    vault_dirs = [p for p in sorted(vaults_root.iterdir())
                  if p.is_dir() and not p.name.startswith(".") and not p.name.startswith("_")]
    vault_names = [v.name for v in vault_dirs]

    # ---------------- entity counts ----------------
    entity_counts: Counter[str] = Counter()
    entity_first: dict[str, dt.date] = {}
    entity_last: dict[str, dt.date] = {}
    entity_vaults: defaultdict[str, set[str]] = defaultdict(set)
    entity_has_note: dict[str, bool] = {}

    # ---------------- daily-note signals ----------------
    daily_goal_records: list[dict[str, Any]] = []   # raw matches before reconciliation
    daily_followup_text: dict[dt.date, str] = {}    # date -> body text (for follow-up search)

    # ---------------- stale projects ----------------
    project_candidates: list[Path] = []

    # ---------------- contacts ----------------
    contact_records: list[dict[str, Any]] = []

    # ---------------- threads ----------------
    thread_records: list[dict[str, Any]] = []

    # ---------------- weekly lookahead ----------------
    weekly_lookahead: list[dict[str, Any]] = []

    # ---------------- frustration ----------------
    frustration_phrases: Counter[str] = Counter()

    # ---------------- sample notes ----------------
    sample_notes: list[Path] = []

    total_notes = 0

    for vault in vault_dirs:
        # In FAST mode, traverse selectively
        if deep:
            md_iter = list(vault.rglob("*.md"))
        else:
            md_iter = []
            # Always include _Index.md and MOC files
            for p in vault.rglob("_Index.md"):
                md_iter.append(p)
            for p in vault.rglob("MOC.md"):
                md_iter.append(p)
            # Project root dirs
            for hint in PROJECT_ROOT_DIR_HINTS:
                root = vault / hint
                if root.is_dir():
                    md_iter.extend(root.rglob("*.md"))
            # Contacts / Threads always (for stakeholder signals)
            for hint in CONTACTS_DIRS + THREADS_DIRS:
                root = vault / hint
                if root.is_dir():
                    md_iter.extend(root.rglob("*.md"))
            # Daily notes — recent only
            daily_root = vault / "Daily" if vault.name == DAILY_VAULT_NAME else None
            if vault.name == DAILY_VAULT_NAME:
                for p in vault.rglob("*.md"):
                    d = daily_date_from_name(p)
                    if d and days_since(d) <= RECENT_DAYS:
                        md_iter.append(p)
                # Weekly notes
                weekly_root = vault / "Weekly"
                if weekly_root.is_dir():
                    md_iter.extend(weekly_root.rglob("*.md"))

        for p in md_iter:
            if not p.is_file():
                continue
            try:
                rel = p.relative_to(vault)
            except ValueError:
                continue
            if is_in_protected(rel):
                continue
            total_notes += 1
            text = safe_read(p)
            if not text:
                continue
            fm, body = parse_frontmatter(text)
            mdate = file_mdate(p)

            # entity counts (wikilinks)
            for raw_link in WIKILINK_RE.findall(body):
                name = raw_link.strip().split("#")[0].split("/")[-1]
                if not name:
                    continue
                entity_counts[name] += 1
                entity_vaults[name].add(vault.name)
                if name not in entity_first or mdate < entity_first[name]:
                    entity_first[name] = mdate
                if name not in entity_last or mdate > entity_last[name]:
                    entity_last[name] = mdate

            # daily-note signals (vault Daily)
            if vault.name == DAILY_VAULT_NAME:
                d = daily_date_from_name(p)
                if d:
                    daily_followup_text[d] = body
                    for pat in GOAL_PATTERNS:
                        for m in pat.finditer(body):
                            stated = m.group(1).strip().rstrip(".")
                            if 6 <= len(stated) <= 200:
                                daily_goal_records.append({
                                    "text": stated,
                                    "stated_in": str(rel),
                                    "stated_at": d.isoformat(),
                                })
                    # frustration phrases
                    body_low = body.lower()
                    for fw in FRUSTRATION_WORDS:
                        for fmatch in re.finditer(re.escape(fw), body_low):
                            start = max(0, fmatch.start() - 80)
                            end = min(len(body), fmatch.end() + 80)
                            snippet = body[start:end]
                            for link in WIKILINK_RE.findall(snippet):
                                frustration_phrases[link.strip()] += 1
                # weekly lookahead
                if "Weekly" in rel.parts:
                    section_match = re.search(
                        r"(?is)#{1,3}\s*(?:looking ahead|next week|plans?)\s*\n(.+?)(?:\n#{1,3}|\Z)",
                        body,
                    )
                    if section_match:
                        for line in section_match.group(1).splitlines():
                            item = line.strip(" -*\t")
                            if 8 <= len(item) <= 200 and not item.startswith("#"):
                                weekly_lookahead.append({
                                    "weekly_note": str(rel),
                                    "item": item,
                                })

            # project root candidates
            if any(hint in rel.parts for hint in PROJECT_ROOT_DIR_HINTS):
                # only depth-1 inside the hint dir = project root
                idx = next((i for i, part in enumerate(rel.parts) if part in PROJECT_ROOT_DIR_HINTS), None)
                if idx is not None and len(rel.parts) - idx <= 2:
                    project_candidates.append(p)
                    # Sample project roots in fast mode for context
                    if len(sample_notes) < 60:
                        sample_notes.append(p)

            # contacts
            if any(hint in rel.parts for hint in CONTACTS_DIRS):
                contact_records.append({
                    "name": p.stem,
                    "path": str(rel),
                    "vault": vault.name,
                    "last_modified": mdate.isoformat(),
                    "mention_count": entity_counts.get(p.stem, 0),  # rough, recomputed below
                })

            # threads
            if any(hint in rel.parts for hint in THREADS_DIRS):
                thread_records.append({
                    "name": p.stem,
                    "path": str(rel),
                    "vault": vault.name,
                    "last_modified": mdate.isoformat(),
                    "days_stale": days_since(mdate),
                })

    # ---------------- whether top entities have their own note ----------------
    # Build a lowercase index of all note stems we saw.
    known_stems = set()
    for vault in vault_dirs:
        for p in vault.rglob("*.md"):
            if not p.is_file():
                continue
            rel = p.relative_to(vault)
            if is_in_protected(rel):
                continue
            known_stems.add(p.stem.lower())

    for name in entity_counts:
        entity_has_note[name] = name.lower() in known_stems

    # ---------------- post-process: top entities w/o own note ----------------
    # Filter out noise patterns from bulk export linkage (RAW source stubs).
    NOISE_ENTITY_RE = re.compile(
        r"^(?:conversations?|messages?|msg|takeout|attachment|attach|export|"
        r"thread|chat|email|mail|note|file|doc|page|item)[\-_]?\d+$",
        re.I,
    )
    top_entities = []
    for name, count in entity_counts.most_common(200):
        if len(name) < 2:
            continue
        if name.isdigit():
            continue
        if NOISE_ENTITY_RE.match(name):
            continue
        if not entity_first.get(name):
            continue
        top_entities.append({
            "name": name,
            "mention_count": count,
            "has_own_note": entity_has_note.get(name, False),
            "first_mentioned": entity_first[name].isoformat(),
            "last_mentioned": entity_last[name].isoformat(),
            "days_since_last": days_since(entity_last[name]),
            "vaults": sorted(entity_vaults[name]),
        })
    # Focus on the "most interesting" — recurring + no dedicated note OR went quiet
    top_entities_focus = [
        e for e in top_entities
        if (e["mention_count"] >= 5 and not e["has_own_note"])
        or (e["mention_count"] >= 10 and e["days_since_last"] >= 90)
    ][:40]

    # ---------------- stated goals reconciliation ----------------
    stated_goals_out: list[dict[str, Any]] = []
    for g in daily_goal_records:
        stated_at = dt.date.fromisoformat(g["stated_at"])
        # Build a search needle from the goal text (first ~6 significant words, lowercased)
        words = re.findall(r"[a-zA-Z][a-zA-Z\-']{2,}", g["text"].lower())
        if len(words) < 2:
            continue
        needle = " ".join(words[:5])
        # Check subsequent daily notes within DROPPED_GOAL_FOLLOWUP_DAYS
        still_mentioned = False
        last_seen = stated_at
        for d, body in daily_followup_text.items():
            if d <= stated_at:
                continue
            if d > stated_at + dt.timedelta(days=DROPPED_GOAL_FOLLOWUP_DAYS):
                continue
            # crude fuzzy: any 3 consecutive words from needle present
            tokens = needle.split()
            for i in range(len(tokens) - 2):
                bigram = " ".join(tokens[i:i+3])
                if bigram in body.lower():
                    still_mentioned = True
                    last_seen = max(last_seen, d)
                    break
            if still_mentioned:
                break
        days_quiet = (today - last_seen).days
        if not still_mentioned and days_quiet >= 30:
            stated_goals_out.append({
                "text": g["text"][:200],
                "stated_in": g["stated_in"],
                "stated_at": g["stated_at"],
                "still_mentioned": False,
                "days_since_last_mention": days_quiet,
            })
    # Sort: oldest abandoned first (those quietest the longest)
    stated_goals_out.sort(key=lambda x: x["days_since_last_mention"], reverse=True)
    stated_goals_out = stated_goals_out[:40]

    # ---------------- stale projects ----------------
    stale_projects_out = []
    for p in project_candidates:
        mdate = file_mdate(p)
        days_stale = days_since(mdate)
        if days_stale >= STALE_PROJECT_DAYS:
            try:
                rel = p.relative_to(vaults_root)
            except ValueError:
                rel = p.name
            wikicount = entity_counts.get(p.stem, 0)
            stale_projects_out.append({
                "path": str(rel),
                "name": p.stem,
                "last_modified": mdate.isoformat(),
                "days_stale": days_stale,
                "mention_count": wikicount,
            })
    stale_projects_out.sort(key=lambda x: (-x["mention_count"], -x["days_stale"]))
    stale_projects_out = stale_projects_out[:30]

    # ---------------- quiet contacts ----------------
    # Recompute mention count properly using entity_counts.
    quiet_contacts_out = []
    for c in contact_records:
        mc = entity_counts.get(c["name"], 0)
        if mc < 3:
            continue
        last_m = dt.date.fromisoformat(c["last_modified"])
        last_seen = entity_last.get(c["name"], last_m)
        days_quiet = days_since(last_seen)
        if days_quiet >= QUIET_CONTACT_DAYS:
            quiet_contacts_out.append({
                "name": c["name"],
                "path": c["path"],
                "vault": c["vault"],
                "mention_count": mc,
                "last_mentioned": last_seen.isoformat(),
                "days_quiet": days_quiet,
            })
    quiet_contacts_out.sort(key=lambda x: (-x["mention_count"], -x["days_quiet"]))
    quiet_contacts_out = quiet_contacts_out[:30]

    # ---------------- frustration phrases ----------------
    frustration_out = [
        {"entity": name, "co_occurrences_with_frustration": count}
        for name, count in frustration_phrases.most_common(20)
        if count >= 2
    ]

    # ---------------- dropped threads ----------------
    dropped_threads_out = sorted(
        [t for t in thread_records if t["days_stale"] >= 60],
        key=lambda x: -x["days_stale"],
    )[:30]

    # ---------------- weekly lookahead misses ----------------
    # Cross-check: did any of these items show up in subsequent daily notes?
    weekly_with_status: list[dict[str, Any]] = []
    daily_body_concat_recent = "\n".join(
        body for d, body in daily_followup_text.items()
        if days_since(d) <= RECENT_DAYS
    ).lower()
    for w in weekly_lookahead[-40:]:  # most recent ~40
        words = re.findall(r"[a-zA-Z][a-zA-Z\-']{2,}", w["item"].lower())
        if len(words) < 2:
            continue
        executed = False
        for i in range(len(words) - 1):
            bigram = words[i] + " " + words[i+1]
            if bigram in daily_body_concat_recent:
                executed = True
                break
        weekly_with_status.append({
            "item": w["item"][:200],
            "weekly_note": w["weekly_note"],
            "executed": executed,
        })
    weekly_with_status = [w for w in weekly_with_status if not w["executed"]][:30]

    # ---------------- sample notes for Claude ----------------
    # Mix: top stale projects, top quiet contacts' notes, most recent Daily/Weekly
    extra_samples: list[Path] = []
    for sp in stale_projects_out[:8]:
        extra_samples.append(vaults_root / sp["path"])
    for c in quiet_contacts_out[:6]:
        extra_samples.append(vaults_root / c["vault"] / c["path"])
    # Latest 5 Daily notes
    daily_vault = vaults_root / DAILY_VAULT_NAME
    if daily_vault.exists():
        dailies = [(daily_date_from_name(p), p) for p in daily_vault.rglob("*.md")]
        dailies = sorted([d for d in dailies if d[0]], key=lambda x: x[0], reverse=True)[:5]
        for _, p in dailies:
            extra_samples.append(p)
    # Latest Weekly note
    weekly_vault = vaults_root / DAILY_VAULT_NAME / "Weekly"
    if weekly_vault.exists():
        weeklies = sorted(weekly_vault.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:2]
        extra_samples.extend(weeklies)

    seen_samples = set()
    sample_notes_out: list[str] = []
    for p in (sample_notes + extra_samples):
        try:
            rel = p.relative_to(vaults_root)
        except ValueError:
            continue
        s = str(rel)
        if s in seen_samples:
            continue
        seen_samples.add(s)
        sample_notes_out.append(s)
    sample_notes_out = sample_notes_out[:50]

    return GatherResult(
        generated_at=today.isoformat(),
        depth="deep" if deep else "fast",
        vaults_scanned=vault_names,
        totals={
            "notes_scanned": total_notes,
            "unique_entities": len(entity_counts),
            "stated_goals_found": len(daily_goal_records),
        },
        top_entities=top_entities_focus,
        stated_goals=stated_goals_out,
        stale_projects=stale_projects_out,
        quiet_contacts=quiet_contacts_out,
        frustration_phrases=frustration_out,
        weekly_lookahead=weekly_with_status,
        dropped_threads=dropped_threads_out,
        sample_notes=sample_notes_out,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deep", action="store_true",
                        help="Walk the entire vault. Default: fast (indexes + recent only).")
    parser.add_argument("--vaults-root", default=str(DEFAULT_VAULTS_ROOT))
    parser.add_argument("--out", default=None,
                        help="Where to write the JSON digest. Default: _scripts/.blind-spots-digest-<date>.json")
    args = parser.parse_args(argv)

    vaults_root = Path(args.vaults_root)
    if not vaults_root.exists():
        print(f"vaults root not found: {vaults_root}", file=sys.stderr)
        return 2

    result = gather(vaults_root, deep=args.deep)

    today = dt.date.today().isoformat()
    out_path = Path(args.out) if args.out else DEFAULT_SCRIPTS_DIR / f".blind-spots-digest-{today}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")

    print(f"Digest written: {out_path}")
    print(f"  depth:               {result.depth}")
    print(f"  vaults scanned:      {len(result.vaults_scanned)}")
    print(f"  notes scanned:       {result.totals['notes_scanned']}")
    print(f"  top entities:        {len(result.top_entities)}")
    print(f"  stated goals (open): {len(result.stated_goals)}")
    print(f"  stale projects:      {len(result.stale_projects)}")
    print(f"  quiet contacts:      {len(result.quiet_contacts)}")
    print(f"  frustration links:   {len(result.frustration_phrases)}")
    print(f"  weekly lookahead:    {len(result.weekly_lookahead)}")
    print(f"  dropped threads:     {len(result.dropped_threads)}")
    print(f"  sample notes:        {len(result.sample_notes)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
