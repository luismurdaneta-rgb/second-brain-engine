#!/usr/bin/env python3
"""
situation_data.py — extract a snapshot of attention demands from the vault.

Walks the Second Brain vault and emits a single JSON document describing every
item that's currently competing for your attention, with heuristic
importance and urgency scores (1–5 each).

Item types:
  • email_reply  — a thread where the latest message is from someone else
                   and the user hasn't replied (≤14d old)
  • event        — a calendar event in the next 14 days, with `needsAction`
                   RSVPs flagged as urgent
  • commitment   — a bullet from "Ideas & open questions" / "What didn't
                   get done" sections in recent daily notes (last 7d).
                   Bullets marked resolved are skipped — see
                   ``is_resolved_bullet`` for the supported markers
                   (strikethrough ``~~...~~``, ``[x] `` / ``[done] ``,
                   ``✅``, or inline ``<!-- done -->``).
  • waiting_on   — a thread where the user sent the last message ≥3d ago and
                   no reply has come

Importance scale (1–5, family-first weighting):
  5: Family / personal-life critical (family, health, legal)
  4: Academic / TFM / health / direct asks from named contacts
  3: Work, admin from real humans, bills with deadlines
  2: Routine admin / low-stakes coordination
  1: Bot mail, OTPs, automated notifications

Urgency scale (1–5):
  5: Today
  4: Tomorrow / next 2 days
  3: This week (≤7 days)
  2: Within 14 days
  1: Stale / no deadline

Output schema:
{
  "generated_at": "<ISO 8601 UTC>",
  "today": "YYYY-MM-DD",
  "items": [
    {
      "id": "<stable-hash>",
      "type": "email_reply" | "event" | "commitment" | "waiting_on",
      "title": "...",
      "detail": "...",
      "source": "wikilink or computer:// URL",
      "stakeholders": ["..."],
      "importance": 1–5,
      "urgency": 1–5,
      "age_days": <int>,
      "due_in_days": <int|null>,
      "tags": ["family"|"academic"|"admin"|"work"|"personal"|...]
    },
    ...
  ],
  "summary": {
    "by_type": {"email_reply": N, ...},
    "by_quadrant": {"q1": N, "q2": N, "q3": N, "q4": N},
    "stakeholders": [{"name": "...", "count": N}, ...]
  }
}

Usage:
    python3 situation_data.py                          # writes to stdout
    python3 situation_data.py --pretty                 # pretty-printed
    python3 situation_data.py --vault-root <path>
    python3 situation_data.py --output <path>          # write to file
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta, date as date_cls
from pathlib import Path

import lib_vault
from typing import Optional

ME_EMAIL = "you@example.com"

# ── Heuristic dictionaries ─────────────────────────────────────────────────────

# Words / contact emails that boost importance to 5 (family-first)
FAMILY_TOKENS = [
    # customize these to your own high-priority signal terms:
    "family", "legal", "health", "custody", "urgent",
    "robinette", "co-parent", "co parent", "parental",
]
LEGAL_TOKENS = [
    "lawyer", "advogado", "court", "tribunal", "juridico", "legal",
    "police", "polic[ií]a", "comiss[aã]o",
]
ACADEMIC_TOKENS = [
    "tfm", "zigurat", "tatiana pedrosa", "tf.pedrosa",
    "dissert", "thesis", "tese", "orientação",
    "canvas", "instructure",
]
HEALTH_TOKENS = ["medic", "doctor", "doutor", "hospital", "clinica", "saude", "exam"]
WORK_TOKENS = ["dreamworks", "archistar", "autodesk", "revit", "censusone", "client"]
ADMIN_TOKENS = ["fatura", "invoice", "recibo", "meo", "credito agricola",
                "seguranca social", "irs", "tax", "subsidy"]
BOT_SENDERS = [
    "no-reply", "noreply", "notifications@", "mensagens@",
    "academy-support@", "support+", "billing@", "automated@",
    "noreply@signin", "no-reply@accounts",
]


# ── Helpers ────────────────────────────────────────────────────────────────────


def parse_frontmatter(text: str) -> dict:
    return lib_vault.frontmatter_dict(text)


def stable_id(*parts: str) -> str:
    h = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return h[:12]


def matches_any(haystack: str, needles: list[str]) -> bool:
    h = haystack.lower()
    for n in needles:
        if re.search(n, h):
            return True
    return False


def is_bot_sender(email: str) -> bool:
    e = email.lower()
    return any(b in e for b in BOT_SENDERS)


# ── Importance / Urgency scoring ───────────────────────────────────────────────


def score_importance(title: str, stakeholders: list[str], tags: list[str]) -> int:
    blob = (title + " " + " ".join(stakeholders) + " " + " ".join(tags)).lower()
    if matches_any(blob, FAMILY_TOKENS) or matches_any(blob, LEGAL_TOKENS):
        return 5
    if matches_any(blob, ACADEMIC_TOKENS) or matches_any(blob, HEALTH_TOKENS):
        return 4
    if matches_any(blob, WORK_TOKENS):
        return 3
    # bot or admin — lower
    if any(is_bot_sender(s) for s in stakeholders):
        return 1
    if matches_any(blob, ADMIN_TOKENS):
        return 2
    # default mid
    return 3


def score_urgency(due_in_days: Optional[int], age_days: int) -> int:
    if due_in_days is not None:
        if due_in_days <= 0:
            return 5
        if due_in_days <= 2:
            return 4
        if due_in_days <= 7:
            return 3
        if due_in_days <= 14:
            return 2
        return 1
    # age-based for items without explicit deadline
    if age_days >= 7:
        return 4
    if age_days >= 3:
        return 3
    if age_days >= 1:
        return 2
    return 1


def quadrant(importance: int, urgency: int) -> str:
    """Eisenhower Q1=urgent+important, Q2=important not urgent,
    Q3=urgent not important, Q4=neither."""
    imp_high = importance >= 4
    urg_high = urgency >= 4
    if imp_high and urg_high:
        return "q1"
    if imp_high and not urg_high:
        return "q2"
    if not imp_high and urg_high:
        return "q3"
    return "q4"


def classify_tags(title: str, stakeholders: list[str]) -> list[str]:
    blob = (title + " " + " ".join(stakeholders)).lower()
    tags = []
    if matches_any(blob, FAMILY_TOKENS):
        tags.append("family")
    if matches_any(blob, LEGAL_TOKENS):
        tags.append("legal")
    if matches_any(blob, ACADEMIC_TOKENS):
        tags.append("academic")
    if matches_any(blob, HEALTH_TOKENS):
        tags.append("health")
    if matches_any(blob, WORK_TOKENS):
        tags.append("work")
    if matches_any(blob, ADMIN_TOKENS):
        tags.append("admin")
    if any(is_bot_sender(s) for s in stakeholders):
        tags.append("bot")
    return tags or ["other"]


# ── Sources ────────────────────────────────────────────────────────────────────


def collect_email_demands(vault_root: Path, today: date_cls) -> list[dict]:
    """Threads where last message is from someone else, ≤14d old, you haven't replied."""
    threads_dir = vault_root / "Google Data" / "Threads"
    if not threads_dir.exists():
        return []

    line_re = re.compile(
        r"^\s*-\s+(\d{4}-\d{2}-\d{2})\s+—\s+(\S+@\S+)\s+—\s+\[\[(.+?)(\|.*)?\]\]"
    )
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
                msgs.append({
                    "date": m.group(1),
                    "sender": m.group(2).lower(),
                    "link": m.group(3),
                })
        if not msgs:
            continue
        msgs.sort(key=lambda x: x["date"])
        last = msgs[-1]
        if last["sender"] == ME_EMAIL:
            # Waiting-on-others case (handled separately below)
            try:
                last_d = datetime.strptime(last["date"], "%Y-%m-%d").date()
            except ValueError:
                continue
            age = (today - last_d).days
            if age < 3 or age > 14:
                continue
            fm = parse_frontmatter(text)
            subject = fm.get("subject", f.stem)
            recipient = ""
            # find the sender of the previous message (the one waiting on me to reply to)
            for m2 in reversed(msgs[:-1]):
                if m2["sender"] != ME_EMAIL:
                    recipient = m2["sender"]
                    break
            stakeholders = [recipient] if recipient else []
            tags = classify_tags(subject, stakeholders) + ["waiting_on_other"]
            imp = score_importance(subject, stakeholders, tags)
            urg = score_urgency(None, age)
            out.append({
                "id": stable_id("waiting", f.stem),
                "type": "waiting_on",
                "title": f"Waiting for reply: {subject}",
                "detail": f"You sent the last message {age} day(s) ago; no reply yet.",
                "source": f"[[{f.stem}]]",
                "stakeholders": stakeholders,
                "importance": imp,
                "urgency": urg,
                "age_days": age,
                "due_in_days": None,
                "tags": tags,
            })
            continue

        # last is from someone else — needs reply path
        try:
            last_d = datetime.strptime(last["date"], "%Y-%m-%d").date()
        except ValueError:
            continue
        age = (today - last_d).days
        if age < 1 or age > 14:
            continue
        # Has the user replied since?
        me_reply = any(
            m["sender"] == ME_EMAIL and m["date"] >= last["date"] for m in msgs
        )
        if me_reply:
            continue
        fm = parse_frontmatter(text)
        subject = fm.get("subject", f.stem)
        stakeholders = [last["sender"]]
        tags = classify_tags(subject, stakeholders) + ["needs_reply"]
        imp = score_importance(subject, stakeholders, tags)
        urg = score_urgency(None, age)
        out.append({
            "id": stable_id("reply", f.stem),
            "type": "email_reply",
            "title": subject[:120],
            "detail": f"Last message from {last['sender']} {age} day(s) ago.",
            "source": f"[[{f.stem}]]",
            "stakeholders": stakeholders,
            "importance": imp,
            "urgency": urg,
            "age_days": age,
            "due_in_days": None,
            "tags": tags,
        })
    return out


def collect_event_demands(vault_root: Path, today: date_cls) -> list[dict]:
    cal_dir = vault_root / "Google Data" / "Calendar"
    if not cal_dir.exists():
        return []

    out: list[dict] = []
    horizon = today + timedelta(days=14)
    for year_dir in sorted(cal_dir.iterdir()):
        if not year_dir.is_dir():
            continue
        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir():
                continue
            for f in sorted(month_dir.glob("*.md")):
                try:
                    text = f.read_text(encoding="utf-8", errors="ignore")
                    fm = parse_frontmatter(text)
                except Exception:
                    continue
                date_str = fm.get("date", "")
                if not date_str:
                    continue
                try:
                    d = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if d < today or d > horizon:
                    continue
                if fm.get("status") == "cancelled":
                    continue
                summary = fm.get("summary", "(no title)")
                my_response = fm.get("my_response", "")
                # parse attendees from frontmatter (multi-line list)
                stakeholders = []
                in_attendees = False
                for line in text.splitlines():
                    if line.startswith("attendees:"):
                        in_attendees = True
                        continue
                    if in_attendees:
                        m = re.match(r'^\s+-\s+"?([^"\s]+)"?\s*$', line)
                        if m:
                            stakeholders.append(m.group(1))
                        elif line and not line.startswith(" "):
                            break
                stakeholders = [s for s in stakeholders if s != ME_EMAIL]
                tags = classify_tags(summary, stakeholders) + ["event"]
                if my_response == "needsAction":
                    tags.append("rsvp_pending")
                due_in = (d - today).days
                imp = score_importance(summary, stakeholders, tags)
                # bump for needsAction
                urg = score_urgency(due_in, 0)
                if my_response == "needsAction":
                    urg = max(urg, 4)
                out.append({
                    "id": stable_id("event", f.stem),
                    "type": "event",
                    "title": summary[:120],
                    "detail": f"{d.strftime('%a %b %d')} {fm.get('time','')}–{fm.get('end_time','')}"
                              + (f" — RSVP: {my_response}" if my_response else ""),
                    "source": f"[[{f.stem}]]",
                    "stakeholders": stakeholders,
                    "importance": imp,
                    "urgency": urg,
                    "age_days": 0,
                    "due_in_days": due_in,
                    "tags": tags,
                })
    return out


def collect_claude_chats_today(vault_root: Path, today: date_cls) -> list[dict]:
    """Find Claude conversation notes (web or cowork) whose created or
    last_message date is today."""
    cdir = vault_root / "Claude"
    if not cdir.exists():
        return []
    out: list[dict] = []
    today_iso = today.isoformat()
    for f in cdir.rglob("*.md"):
        if f.name.startswith("_"):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        fm = parse_frontmatter(text)
        if fm.get("type") != "claude-conversation":
            continue
        created = fm.get("created", "")
        last = fm.get("last_message", "") or created
        when = (last or created)[:10]
        if when != today_iso:
            continue
        out.append({
            "title": fm.get("title", f.stem)[:120],
            "source": fm.get("source", "claude"),
            "model": fm.get("model", ""),
            "message_count": fm.get("message_count", ""),
            "tool_call_count": fm.get("tool_call_count", ""),
            "stem": f.stem,
            "topic": fm.get("topic", ""),
            "created": created,
            "last_message": last,
        })
    out.sort(key=lambda x: x.get("last_message") or "", reverse=True)
    return out


def collect_vault_changes_today(vault_root: Path, today: date_cls) -> dict:
    """Count files created/modified today, grouped by top-level vault."""
    import os, time
    today_start = datetime(today.year, today.month, today.day).timestamp()
    today_end = today_start + 86400
    by_vault: Counter = Counter()
    samples: dict[str, list[str]] = {}
    for vault_dir in vault_root.iterdir():
        if not vault_dir.is_dir() or vault_dir.name.startswith("_"):
            continue
        vname = vault_dir.name
        for root, _, files in os.walk(vault_dir):
            for f in files:
                if not f.endswith(".md"):
                    continue
                p = Path(root) / f
                try:
                    mt = p.stat().st_mtime
                except Exception:
                    continue
                if today_start <= mt < today_end:
                    by_vault[vname] += 1
                    samples.setdefault(vname, [])
                    if len(samples[vname]) < 5:
                        samples[vname].append(f.removesuffix(".md"))
    total = sum(by_vault.values())
    return {
        "total": total,
        "by_vault": dict(by_vault.most_common()),
        "samples": samples,
    }


# Resolved-marker detection. A commitment bullet is considered resolved (and
# skipped by the extractor) if it carries any of these markers in the source
# daily note. Markers are removed before scoring so the title doesn't get
# polluted. Apply markers by hand in Obsidian, or via a future
# `resolve_commitment.py <id>` helper.
RESOLVED_PREFIX_RE = re.compile(
    r"^\s*(?:\[(?:x|X|done|DONE|Done)\]|✅|✔️|✔|☑|☑️)\s+"
)
RESOLVED_STRIKETHROUGH_RE = re.compile(r"^\s*~~.+~~\s*$", re.DOTALL)
# Match <!-- done -->, <!-- done 2026-05-26 -->, <!-- done: anything -->, etc.
RESOLVED_HTML_COMMENT_RE = re.compile(
    r"<!--\s*(?:done|resolved)\b[^>]*-->", re.IGNORECASE
)


def is_resolved_bullet(text_item: str) -> bool:
    """True if a bullet from a daily note has been marked done.

    Recognized markers:
      • full-line strikethrough: ``~~bullet text~~``
      • prefix tokens:            ``[x] ``, ``[done] ``, ``✅ ``, ``☑ ``
      • inline HTML comment:     ``<!-- done -->`` / ``<!-- resolved -->``

    Idempotent and conservative — unmarked bullets behave exactly as before.
    """
    if not text_item:
        return False
    if RESOLVED_HTML_COMMENT_RE.search(text_item):
        return True
    if RESOLVED_PREFIX_RE.match(text_item):
        return True
    if RESOLVED_STRIKETHROUGH_RE.match(text_item.strip()):
        return True
    return False


def collect_commitment_demands(vault_root: Path, today: date_cls) -> list[dict]:
    out = []
    for i in range(1, 8):
        d = today - timedelta(days=i)
        p = (vault_root / "Daily" / str(d.year) / f"{d.month:02d}"
             / f"{d.strftime('%Y-%m-%d')}.md")
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
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
            if is_resolved_bullet(text_item):
                continue
            age = (today - d).days
            stakeholders: list[str] = []
            tags = classify_tags(text_item, stakeholders) + ["commitment"]
            imp = score_importance(text_item, stakeholders, tags)
            urg = score_urgency(None, age)
            out.append({
                "id": stable_id("commit", text_item[:80], d.isoformat()),
                "type": "commitment",
                "title": text_item[:120],
                "detail": f"From daily note {d.isoformat()} (age {age}d).",
                "source": f"[[{d.isoformat()}]]",
                "stakeholders": stakeholders,
                "importance": imp,
                "urgency": urg,
                "age_days": age,
                "due_in_days": None,
                "tags": tags,
            })
    return out


# ── Main ───────────────────────────────────────────────────────────────────────


def build_snapshot(vault_root: Path) -> dict:
    today = datetime.now().date()
    items: list[dict] = []
    items.extend(collect_email_demands(vault_root, today))
    items.extend(collect_event_demands(vault_root, today))
    items.extend(collect_commitment_demands(vault_root, today))

    # Stable sort: by quadrant priority then urgency desc then importance desc
    quad_priority = {"q1": 0, "q3": 1, "q2": 2, "q4": 3}
    items.sort(key=lambda x: (
        quad_priority[quadrant(x["importance"], x["urgency"])],
        -x["urgency"],
        -x["importance"],
    ))

    by_type: Counter = Counter(it["type"] for it in items)
    by_quadrant: Counter = Counter(quadrant(it["importance"], it["urgency"]) for it in items)
    stakeholder_counts: Counter = Counter()
    for it in items:
        for s in it.get("stakeholders", []):
            stakeholder_counts[s] += 1
    top_stakeholders = [{"name": k, "count": v} for k, v in stakeholder_counts.most_common(15)]

    today_activity = {
        "claude_chats": collect_claude_chats_today(vault_root, today),
        "vault_changes": collect_vault_changes_today(vault_root, today),
        # Cowork sessions are filled live by the dashboard via window.cowork.callMcpTool
        # because they require MCP access that this script doesn't have.
        "cowork_sessions_live": True,
        # Claude Code activity requires mounting ~/.claude/ — not yet wired.
        "claude_code_available": False,
    }

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "today": today.isoformat(),
        "items": items,
        "today_activity": today_activity,
        "summary": {
            "total": len(items),
            "by_type": dict(by_type),
            "by_quadrant": dict(by_quadrant),
            "stakeholders": top_stakeholders,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault-root", default=str(lib_vault.vaults_root()),
                    help="Path to Vaults/ root")
    ap.add_argument("--output", help="Write JSON to this path (default: stdout)")
    ap.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = ap.parse_args()

    vault_root = Path(args.vault_root)
    if not vault_root.exists():
        print(f"ERROR: vault root {vault_root} does not exist", file=sys.stderr)
        return 2

    snapshot = build_snapshot(vault_root)
    if args.pretty:
        out = json.dumps(snapshot, indent=2, ensure_ascii=False)
    else:
        out = json.dumps(snapshot, ensure_ascii=False)

    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"Wrote {len(snapshot['items'])} items to {args.output}", file=sys.stderr)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
