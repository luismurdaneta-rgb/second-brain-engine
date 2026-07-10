#!/usr/bin/env python3
"""navigator_gather.py — stage-1 mechanical gather for the second-brain-navigator skill.

The navigator tracks your behavior patterns longitudinally and scores movement
toward the One-Year-Vision-2027 pillars. This script does the mechanical part so
the skill (Claude, stage 2) reads one JSON digest instead of walking 7k notes.

Signals gathered (rule-based, no LLM):
  - window_activity    : notes touched per vault per day, this window vs previous
  - daily_coverage     : which days in the window have a daily note; streak/gap info
  - pattern_hits       : per ledger pattern (Vaults/Personal/Patterns/*.md), count of
                         signal matches in window notes vs previous window
  - pillar_mentions    : per 2027-vision pillar, mention counts this window vs previous,
                         broken down by vault (so sync-artifact vaults can be discounted)
  - goals_stated       : intention phrases parsed from window daily notes
  - open_loops         : unchecked checkboxes in window daily notes
  - checkbox_rate      : done vs open checkboxes in window daily notes
  - sample_notes       : curated paths stage 2 should actually read

Output: JSON digest at `_scripts/.navigator-digest-<YYYY-MM-DD>.json` (idempotent —
re-running the same day overwrites).

Run:
  python3 navigator_gather.py                 # default 7-day window
  python3 navigator_gather.py --window-days 14
  python3 navigator_gather.py --out /tmp/digest.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import lib_vault

DEFAULT_WINDOW_DAYS = 7
PER_FILE_MENTION_CAP = 10  # one bulk note shouldn't dominate a pillar count

PROTECTED_DIRS = {".obsidian", "_Quarantine", "_archive_chatgpt", "_Archive"}

# Vaults whose mtimes reflect SYNC time, not event time. Counted, but reported
# separately so stage 2 can discount them.
SYNC_VAULTS = {"Whatsapp", "Google Data", "ChatGPT", "Claude"}

DAILY_VAULT = "Daily"
PATTERNS_SUBPATH = Path("Personal") / "Patterns"

# Derivative/meta notes (analysis ABOUT behavior, incl. this skill's own outputs).
# Excluded from pattern-hit and pillar counts so the ledger doesn't feed on itself;
# still counted in window activity.
META_REL_DIRS = (
    "Personal/Patterns", "Personal/Claude-Notes",
    "Daily/Navigator", "Daily/Blind-spots",
    "Daily/Architecture-audits", "Daily/Action-items",
)

DATE_IN_NAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
CHECKBOX_OPEN_RE = re.compile(r"(?m)^\s*-\s*\[ \]\s+(.+)$")
CHECKBOX_DONE_RE = re.compile(r"(?m)^\s*-\s*\[[xX]\]\s+")

GOAL_PATTERNS = [
    re.compile(r"(?im)^\s*(?:goal|intent|intention|plan|next step[s]?)\s*[:\-]\s*(.+)$"),
    re.compile(r"(?im)\bI(?:'m| am)? going to\s+([^.!?\n]{6,140})"),
    re.compile(r"(?im)\bI want to\s+([^.!?\n]{6,140})"),
    re.compile(r"(?im)\bI need to\s+([^.!?\n]{6,140})"),
    re.compile(r"(?im)\bthis week I\s+([^.!?\n]{6,140})"),
]

# The seven pillars of One-Year-Vision-2027 (Vaults/Personal/Profile/).
# Keywords are matched with word boundaries, case-insensitive, EN + ES/PT.
PILLARS: dict[str, list[str]] = {
    "home": ["house", "rent", "lease", "apartment", "van", "casa", "alugar"],
    "work_clients": ["retainer", "client", "cliente", "proposal", "pitch",
                     "invoice", "consulting", "portfolio", "linkedin"],
    "money": ["money", "income", "savings", "budget", "broke", "dinheiro", "plata"],
    "relationships": ["family", "partner", "custody", "visitation"],
    "family": ["mother", "mom", "mamá", "mãe", "alzheimer", "sister",
               "hermana", "venezuela"],
    "faith": ["prayer", "pray", "mass", "church", "rosary", "misa", "rezar", "god"],
    "people": ["aldo", "bruno", "scotty", "mandala", "friend", "amigo"],
}


# ---------- helpers ----------

def compile_kw(words: list[str]) -> list[re.Pattern]:
    return [re.compile(rf"\b{re.escape(w)}\b", re.IGNORECASE) for w in words]


PILLAR_RES = {k: compile_kw(v) for k, v in PILLARS.items()}


def safe_read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def is_protected(rel: Path) -> bool:
    return bool(set(rel.parts) & PROTECTED_DIRS)


def is_meta(rel: Path) -> bool:
    rel_s = rel.as_posix()
    return any(rel_s.startswith(m + "/") or rel_s == m for m in META_REL_DIRS)


def note_date(p: Path) -> dt.date:
    """Prefer a YYYY-MM-DD in the filename (daily notes); else mtime."""
    m = DATE_IN_NAME_RE.search(p.stem)
    if m:
        try:
            return dt.date.fromisoformat(m.group(1))
        except ValueError:
            pass
    return dt.date.fromtimestamp(p.stat().st_mtime)


def parse_signals(fm: dict) -> list[str]:
    """Pattern notes carry `signals: ["a", "b"]` (inline JSON-style list of
    plain substrings). Tolerate a YAML block list too."""
    raw = fm.get("signals")
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(s) for s in raw if str(s).strip()]
    s = str(raw).strip()
    if s.startswith("["):
        try:
            return [str(x) for x in json.loads(s.replace("'", '"'))]
        except Exception:
            return [t.strip(" '\"") for t in s.strip("[]").split(",") if t.strip()]
    return [s] if s else []


def count_hits(patterns: list[re.Pattern], text: str) -> int:
    n = 0
    for rx in patterns:
        n += len(rx.findall(text))
        if n >= PER_FILE_MENTION_CAP:
            return PER_FILE_MENTION_CAP
    return n


# ---------- main gather ----------

def gather(vaults_root: Path, window_days: int) -> dict:
    today = dt.date.today()
    win_start = today - dt.timedelta(days=window_days)
    prev_start = today - dt.timedelta(days=window_days * 2)

    # ---- collect candidate notes by date bucket ----
    window_notes: list[tuple[Path, dt.date, str]] = []   # (path, date, vault)
    prev_notes: list[tuple[Path, dt.date, str]] = []

    for vault_dir in sorted(d for d in vaults_root.iterdir() if d.is_dir()):
        vault = vault_dir.name
        if vault.startswith("_"):
            continue
        for p in vault_dir.rglob("*.md"):
            rel = p.relative_to(vaults_root)
            if is_protected(rel):
                continue
            try:
                d = note_date(p)
            except OSError:
                continue
            if d >= win_start:
                window_notes.append((p, d, vault))
            elif d >= prev_start:
                prev_notes.append((p, d, vault))

    # ---- window activity ----
    activity: dict[str, dict] = {}
    by_vault = Counter(v for _, _, v in window_notes)
    by_vault_prev = Counter(v for _, _, v in prev_notes)
    by_day = Counter(d.isoformat() for _, d, _ in window_notes)
    activity = {
        "window_notes_touched": len(window_notes),
        "previous_window_notes_touched": len(prev_notes),
        "by_vault": dict(by_vault.most_common()),
        "by_vault_previous": dict(by_vault_prev.most_common()),
        "by_day": dict(sorted(by_day.items())),
        "sync_vaults_note": sorted(SYNC_VAULTS & set(by_vault)),
    }

    # ---- daily coverage + goals + checkboxes ----
    daily_dir = vaults_root / DAILY_VAULT
    daily_days: set[str] = set()
    goals: list[dict] = []
    open_loops: list[dict] = []
    done_count = open_count = 0

    daily_window = [(p, d) for p, d, v in window_notes if v == DAILY_VAULT]
    for p, d in sorted(daily_window, key=lambda t: t[1]):
        text = safe_read(p)
        if DATE_IN_NAME_RE.search(p.stem):
            daily_days.add(d.isoformat())
        for rx in GOAL_PATTERNS:
            for m in rx.findall(text):
                g = m.strip().rstrip(".")
                if 6 <= len(g) <= 140:
                    goals.append({"date": d.isoformat(), "text": g,
                                  "file": str(p.relative_to(vaults_root))})
        opens = CHECKBOX_OPEN_RE.findall(text)
        open_count += len(opens)
        done_count += len(CHECKBOX_DONE_RE.findall(text))
        for item in opens[:5]:
            open_loops.append({"date": d.isoformat(), "text": item.strip()[:140],
                               "file": str(p.relative_to(vaults_root))})

    expected = {(win_start + dt.timedelta(days=i)).isoformat()
                for i in range(window_days)}
    daily_coverage = {
        "days_with_note": sorted(daily_days),
        "days_missing": sorted(expected - daily_days),
        "coverage": f"{len(daily_days & expected)}/{window_days}",
    }

    # ---- pattern ledger hits ----
    patterns_dir = vaults_root / PATTERNS_SUBPATH
    pattern_hits: list[dict] = []
    if patterns_dir.exists():
        for note in sorted(patterns_dir.glob("*.md")):
            if note.name.startswith("_"):
                continue
            fm, _ = lib_vault.parse_frontmatter(safe_read(note))
            signals = parse_signals(fm if isinstance(fm, dict) else {})
            entry = {
                "pattern_id": (fm.get("pattern-id") if isinstance(fm, dict) else None)
                              or note.stem,
                "file": str(note.relative_to(vaults_root)),
                "status": fm.get("status", "?") if isinstance(fm, dict) else "?",
                "signals": signals,
                "window_hits": 0,
                "previous_window_hits": 0,
                "hit_files": [],
            }
            if signals:
                rxs = compile_kw(signals)
                for p, _, vault in window_notes:
                    rel = p.relative_to(vaults_root)
                    if is_meta(rel):
                        continue
                    n = count_hits(rxs, safe_read(p))
                    if n:
                        entry["window_hits"] += n
                        if len(entry["hit_files"]) < 8:
                            entry["hit_files"].append(str(rel))
                for p, _, vault in prev_notes:
                    if is_meta(p.relative_to(vaults_root)):
                        continue
                    entry["previous_window_hits"] += count_hits(rxs, safe_read(p))
            else:
                entry["detection"] = "qualitative — no mechanical signals; assess from sample notes"
            pattern_hits.append(entry)

    # ---- pillar mentions ----
    def pillar_counts(notes: list[tuple[Path, dt.date, str]]) -> dict[str, dict]:
        out: dict[str, dict] = {k: {"total": 0, "by_vault": defaultdict(int)}
                                for k in PILLARS}
        for p, _, vault in notes:
            if is_meta(p.relative_to(vaults_root)):
                continue
            text = safe_read(p)
            for name, rxs in PILLAR_RES.items():
                n = count_hits(rxs, text)
                if n:
                    out[name]["total"] += n
                    out[name]["by_vault"][vault] += n
        for v in out.values():
            v["by_vault"] = dict(sorted(v["by_vault"].items(),
                                        key=lambda kv: -kv[1]))
        return out

    pillars_now = pillar_counts(window_notes)
    pillars_prev = pillar_counts(prev_notes)
    pillar_mentions = {
        name: {
            "window": pillars_now[name]["total"],
            "previous_window": pillars_prev[name]["total"],
            "by_vault": pillars_now[name]["by_vault"],
        }
        for name in PILLARS
    }

    # ---- sample notes for stage 2 ----
    samples: list[str] = []
    for p, _ in sorted(daily_window, key=lambda t: t[1], reverse=True)[:7]:
        samples.append(str(p.relative_to(vaults_root)))
    weekly_dir = daily_dir / "Weekly"
    if weekly_dir.exists():
        weeklies = sorted(weekly_dir.glob("*.md"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
        samples.extend(str(p.relative_to(vaults_root)) for p in weeklies[:2])
    for entry in pattern_hits:
        samples.extend(entry["hit_files"][:2])
    seen, deduped = set(), []
    for s in samples:
        if s not in seen:
            seen.add(s)
            deduped.append(s)

    return {
        "generated": today.isoformat(),
        "window_days": window_days,
        "window": {"start": win_start.isoformat(), "end": today.isoformat()},
        "activity": activity,
        "daily_coverage": daily_coverage,
        "goals_stated": goals[:40],
        "open_loops": open_loops[:30],
        "checkboxes": {"done": done_count, "open": open_count},
        "pattern_hits": pattern_hits,
        "pillar_mentions": pillar_mentions,
        "sample_notes": deduped[:25],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    ap.add_argument("--vaults-root", type=Path, default=lib_vault.vaults_root())
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    digest = gather(args.vaults_root, args.window_days)
    out = args.out or (lib_vault.scripts_dir()
                       / f".navigator-digest-{digest['generated']}.json")
    out.write_text(json.dumps(digest, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    ph = digest["pattern_hits"]
    print(f"digest → {out}")
    print(f"  window notes: {digest['activity']['window_notes_touched']} | "
          f"daily coverage: {digest['daily_coverage']['coverage']} | "
          f"patterns tracked: {len(ph)} | "
          f"goals: {len(digest['goals_stated'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
