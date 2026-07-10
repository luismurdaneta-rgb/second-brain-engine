#!/usr/bin/env python3
"""
extract_action_items.py — sweep all vaults for action items / TODOs and emit a digest.

Reads every markdown file under Second Brain/Vaults/*/ and extracts:

  • Explicit checkboxes: "- [ ] do the thing"
  • TODO/FIXME/ACTION markers: lines starting with TODO:, FIXME:, ACTION:, AI:, NEXT:
  • Imperative asks from briefs: lines beginning with "Action:", "Next step:",
    "Follow up:", "Owed:", "I need to", "I should", "Need to", "Should"
  • "Waiting on <name>" / "Blocked by <name>" patterns (for tracking promises *from* others)
  • Promised replies: bodies that contain "I'll get back to you", "let me check",
    "I'll send", "I'll review" (under Google Data threads written by the user)

The output is a JSON digest grouped by vault, with file path, line number, raw
text, normalized text, category, and a confidence score. The Claude side of the
skill then reads the JSON, dedupes against prior runs, and produces:

  1. An append-only TASKS.md update at the vault root.
  2. A dated markdown note at Vaults/Daily/Action-items/<YYYY-MM-DD>-action-items.md.
  3. A Cowork dashboard artifact.

Usage:
    python3 extract_action_items.py
    python3 extract_action_items.py --vault-root "/path/to/your/second-brain/Vaults"
    python3 extract_action_items.py --out /tmp/action_items.json
    python3 extract_action_items.py --days 30        # only files modified in last N days
    python3 extract_action_items.py --include-checked  # also include "- [x]" items (audit mode)

Stdlib only. Idempotent. Read-only on the vault.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, date, timezone
from pathlib import Path

import lib_vault

VAULT_ROOT_DEFAULT = str(lib_vault.vaults_root())

# Skip these directories everywhere — they are quarantine, indexes, or raw.
SKIP_DIR_NAMES = {
    "_Quarantine",
    "_archive_chatgpt",
    "_archive",
    ".obsidian",
    ".trash",
    "node_modules",
    ".git",
}

# Skip these vault-relative path prefixes — they're prompt logs and reference
# material, not commitments. Added 2026-05-28 because the ChatGPT vault was
# producing ~80% of the noise in the rollup (e.g. "Remember to tailor your CV…"
# is an LLM reply, not a the user action item).
SKIP_REL_PATH_PREFIXES = (
    "ChatGPT/2_Areas/",
    "ChatGPT/3_Resources/",
    "ChatGPT/4_Archive/",
)

# Items below this confidence are dropped before dedupe. Categories: checkbox
# (0.95), marker (0.9), brief_ask (0.85) survive; self_commit (0.65) and
# promised_reply (0.7) are filtered. Override via --min-confidence.
DEFAULT_MIN_CONFIDENCE = 0.8

# Only scan these vaults — keep the surface predictable.
VAULTS = [
    "Daily",
    "Google Data",
    "ChatGPT",
    "Claude",
    "Whatsapp",
    "DreamWorks",
    "Zigurat",
    "Personal",
    "Recibos verdes",
]

# ── Regex patterns ────────────────────────────────────────────────────────────

# Unchecked checkbox: - [ ] something  /  * [ ] something  /  1. [ ] something
RE_CHECKBOX_OPEN = re.compile(r"^\s*[-*+]\s+\[\s?\]\s+(.+?)\s*$")
RE_CHECKBOX_DONE = re.compile(r"^\s*[-*+]\s+\[[xX/-]\]\s+(.+?)\s*$")

# Inline markers at start of line (with optional bullet)
RE_MARKER = re.compile(
    r"^\s*(?:[-*+]\s+)?"
    r"(TODO|FIXME|ACTION|NEXT|FOLLOW-?UP|OWED|REPLY|WAITING|BLOCKED)"
    r"\s*:\s+(.+?)\s*$",
    re.IGNORECASE,
)

# Brief-style asks
RE_BRIEF_ASK = re.compile(
    r"^\s*(?:[-*+]\s+)?"
    r"(Action item|Action|Next step|Next steps?|Follow ?up|Follow-?up item|"
    r"To-?do|To do|Owed|Owing|Waiting on|Blocked by|Promised|Commitment)"
    r"\s*[:\-]\s*(.+?)\s*$",
    re.IGNORECASE,
)

# Imperative self-talk in body prose
RE_SELF_COMMIT = re.compile(
    r"^\s*(?:[-*+]\s+)?"
    r"(I need to|I should|I have to|I must|I'?ll (?:need|have) to|"
    r"Need to|Should|Have to|Got to|Gotta|Remember to|Don'?t forget to|"
    r"Make sure to)\s+(.+?)[.!]?\s*$",
    re.IGNORECASE,
)

# Promised reply phrases (used inside threads where the user is the sender)
RE_PROMISED_REPLY = re.compile(
    r"\b(I'?ll (?:get back|send|share|review|check|follow up|circle back|ping|reply)|"
    r"will (?:send|share|review|circle back|follow up|ping you)|"
    r"let me (?:check|review|look into|get back))\b",
    re.IGNORECASE,
)

# Frontmatter sniffing
RE_FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
RE_FM_FIELD = re.compile(r"^([\w_]+):\s*(.*?)\s*$", re.MULTILINE)

# Date-like patterns to surface deadlines from the text
RE_DATE = re.compile(
    r"\b("
    r"\d{4}-\d{2}-\d{2}|"
    r"\d{1,2}/\d{1,2}(?:/\d{2,4})?|"
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day)?|"
    r"(?:tomorrow|today|tonight|EOD|EOW|next week|this week|by Friday|by Monday|by EOD|by EOW)"
    r")\b",
    re.IGNORECASE,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def parse_frontmatter(text: str) -> dict:
    return lib_vault.frontmatter_dict(text)


def strip_frontmatter(text: str) -> str:
    m = RE_FM.match(text)
    return text[m.end():] if m else text


def normalize(s: str) -> str:
    """Used for dedupe — lowercase, collapse whitespace, drop wiki/markdown noise."""
    s = re.sub(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"[`*_~]", "", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def fingerprint(text: str) -> str:
    return hashlib.sha1(normalize(text).encode("utf-8")).hexdigest()[:12]


def iter_md_files(vault_root: Path, vault_name: str, days: int | None):
    base = vault_root / vault_name
    if not base.exists():
        return
    cutoff = None
    if days is not None:
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    for root, dirs, files in os.walk(base):
        # in-place skip
        dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES and not d.startswith(".")]
        for name in files:
            if not name.endswith(".md"):
                continue
            full = Path(root) / name
            # Vault-relative prefix skip — kill prompt-log noise before reading.
            try:
                rel_str = str(full.relative_to(vault_root)).replace(os.sep, "/")
            except ValueError:
                rel_str = ""
            if any(rel_str.startswith(p) for p in SKIP_REL_PATH_PREFIXES):
                continue
            if cutoff is not None:
                try:
                    if full.stat().st_mtime < cutoff:
                        continue
                except OSError:
                    continue
            yield full


def find_dates(text: str) -> list[str]:
    return list({m.group(1) for m in RE_DATE.finditer(text)})


def classify(line: str, fm: dict, file_path: Path, vault_name: str) -> tuple[str, str, float] | None:
    """
    Return (category, normalized_text, confidence) or None if not an action item.

    Categories: 'checkbox', 'marker', 'brief_ask', 'self_commit', 'promised_reply'.
    """
    line = line.rstrip("\n")

    # Drop very short, noisy lines
    if len(line.strip()) < 5:
        return None

    # Skip quoted lines (email reply quotes start with `>`) — those are old
    # text being quoted back, not fresh commitments.
    stripped = line.lstrip()
    if stripped.startswith(">"):
        return None

    m = RE_CHECKBOX_OPEN.match(line)
    if m:
        # Skip checkboxes inside WhatsApp month bundles. Those are transcript
        # content — typically meeting-summary "action items for <person>" blocks
        # pasted into a chat — not your own commitments. Added 2026-06-17 after
        # 8 stale January items (assigned to Barbara/Daniel/Naiara/Tatiana) kept
        # surfacing as the user todos. Durable across re-syncs (sync regenerates the
        # bundle, but the extractor now ignores its checkboxes).
        if vault_name == "Whatsapp" and fm.get("type") == "whatsapp-month":
            return None
        return ("checkbox", m.group(1).strip(), 0.95)

    m = RE_MARKER.match(line)
    if m:
        marker = m.group(1).upper().replace("-", "").replace(" ", "")
        text = m.group(2).strip()
        if marker in {"WAITING", "BLOCKED"}:
            return ("waiting_on", text, 0.85)
        return ("marker", text, 0.9)

    m = RE_BRIEF_ASK.match(line)
    if m:
        text = m.group(2).strip()
        # too generic
        if len(text) < 4:
            return None
        return ("brief_ask", text, 0.85)

    m = RE_SELF_COMMIT.match(line)
    if m:
        verb = m.group(1)
        rest = m.group(2).strip()
        if len(rest) < 4:
            return None
        # Avoid past-tense or hypothetical: skip lines starting with "I should have", "Need to have done"
        if re.match(r"^(have|had)\b", rest, re.IGNORECASE):
            return None
        return ("self_commit", f"{verb} {rest}".strip(), 0.65)

    # Promised reply — only inside email threads where the user is the sender.
    # Set MY_EMAIL_LOCALPART to your own address local-part (the bit before "@").
    my_localpart = os.environ.get("MY_EMAIL_LOCALPART", "your-email")
    if vault_name == "Google Data" and fm.get("from", "").lower().find(my_localpart) >= 0:
        if RE_PROMISED_REPLY.search(line):
            return ("promised_reply", line.strip(), 0.7)

    return None


def vault_of(rel_path: Path) -> str:
    parts = rel_path.parts
    return parts[0] if parts else "?"


# ── Main scan ─────────────────────────────────────────────────────────────────


def scan(vault_root: Path, days: int | None, include_checked: bool,
         min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> dict:
    items_by_vault: dict[str, list[dict]] = defaultdict(list)
    files_scanned = 0
    items_total = 0
    items_dropped_low_conf = 0
    completed_today = 0

    today_iso = date.today().isoformat()

    for vault in VAULTS:
        for f in iter_md_files(vault_root, vault, days):
            files_scanned += 1
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            fm = parse_frontmatter(text)
            body = strip_frontmatter(text)
            rel = f.relative_to(vault_root)

            # Block-scoped tracking: we treat the whole file's frontmatter as context
            for i, line in enumerate(body.splitlines(), 1):
                cls = classify(line, fm, f, vault)
                if cls is None:
                    if include_checked:
                        mdone = RE_CHECKBOX_DONE.match(line)
                        if mdone:
                            completed_today += 1
                    continue
                category, ntext, conf = cls

                # Apply confidence floor before any further work — drops
                # self_commit (0.65) and promised_reply (0.7) by default,
                # which were the bulk of the rollup noise.
                if conf < min_confidence:
                    items_dropped_low_conf += 1
                    continue

                dates = find_dates(ntext)
                items_by_vault[vault].append({
                    "id": fingerprint(f"{rel}|{i}|{ntext}"),
                    "vault": vault,
                    "path": str(rel),
                    "abs_path": str(f),
                    "line": i,
                    "category": category,
                    "text": ntext,
                    "raw": line.strip(),
                    "confidence": conf,
                    "dates_in_text": dates,
                    "file_date": fm.get("date") or fm.get("created") or "",
                    "file_type": fm.get("type") or "",
                    "from": fm.get("from", ""),
                    "to": fm.get("to", ""),
                    "subject": fm.get("subject", "") or fm.get("title", ""),
                })
                items_total += 1

    # De-dupe within each vault by fingerprint of normalized text (across files,
    # near-identical actions are common after recurring briefs).
    deduped: dict[str, list[dict]] = {}
    seen_global: dict[str, dict] = {}
    for vault, items in items_by_vault.items():
        seen_local: set[str] = set()
        out = []
        for it in items:
            fp = fingerprint(it["text"])
            it["text_fp"] = fp
            if fp in seen_local:
                continue
            seen_local.add(fp)
            # Cross-vault dedupe: keep the higher-confidence one
            prior = seen_global.get(fp)
            if prior:
                if it["confidence"] > prior["confidence"]:
                    # remove prior from its vault list
                    deduped[prior["vault"]] = [
                        x for x in deduped[prior["vault"]] if x["text_fp"] != fp
                    ]
                else:
                    continue
            seen_global[fp] = it
            out.append(it)
        deduped[vault] = out

    # Ranking signals
    for vault, items in deduped.items():
        for it in items:
            score = it["confidence"]
            if it["dates_in_text"]:
                score += 0.15
            if it["category"] in {"checkbox", "marker"}:
                score += 0.1
            if it["vault"] == "Daily":
                score += 0.05
            it["score"] = round(score, 3)
        items.sort(key=lambda x: x["score"], reverse=True)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vault_root": str(vault_root),
        "today": today_iso,
        "options": {"days": days, "include_checked": include_checked},
        "summary": {
            "files_scanned": files_scanned,
            "items_total": items_total,
            "items_dropped_low_confidence": items_dropped_low_conf,
            "min_confidence": min_confidence,
            "items_after_dedupe": sum(len(v) for v in deduped.values()),
            "completed_checkboxes_seen": completed_today if include_checked else None,
            "by_vault": {v: len(items) for v, items in deduped.items()},
            "by_category": _by_category(deduped),
        },
        "items": deduped,
    }


def _by_category(deduped: dict[str, list[dict]]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for items in deduped.values():
        for it in items:
            out[it["category"]] += 1
    return dict(out)


# ── CLI ───────────────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Extract action items from all vaults.")
    ap.add_argument("--vault-root", default=VAULT_ROOT_DEFAULT)
    ap.add_argument("--out", default="")
    ap.add_argument("--days", type=int, default=None,
                    help="Only scan files modified in the last N days.")
    ap.add_argument("--include-checked", action="store_true",
                    help="Also count completed checkboxes (for audit mode).")
    ap.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE,
                    help=f"Drop items below this confidence (default {DEFAULT_MIN_CONFIDENCE}). "
                         "Use 0.6 to include self-stated commitments.")
    ap.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")
    args = ap.parse_args(argv)

    vault_root = Path(args.vault_root)
    if not vault_root.exists():
        print(f"vault root not found: {vault_root}", file=sys.stderr)
        return 2

    result = scan(vault_root, args.days, args.include_checked,
                  min_confidence=args.min_confidence)

    out_path = Path(args.out) if args.out else (
        Path("/tmp") / f"action_items_{date.today().isoformat()}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(result, indent=2 if args.pretty else None, ensure_ascii=False),
        encoding="utf-8",
    )
    s = result["summary"]
    print(f"[extract_action_items] scanned={s['files_scanned']} "
          f"items={s['items_total']} "
          f"dropped_low_conf={s['items_dropped_low_confidence']} "
          f"dedup={s['items_after_dedupe']} "
          f"min_conf={s['min_confidence']}")
    print(f"[extract_action_items] by vault: {s['by_vault']}")
    print(f"[extract_action_items] by category: {s['by_category']}")
    print(f"[extract_action_items] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
