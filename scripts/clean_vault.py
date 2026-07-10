#!/usr/bin/env python3
"""clean_vault.py — sweep Second Brain vaults for CLEAR noise.

Conservative cleaner. Three categories:
  marketing-promo   — newsletters, deals, bulk-mail platforms, unsubscribe footers
  bot-notification  — OTPs, password resets, GitHub/Linear/Slack/CI bots, calendar invites
  empty-near-empty  — notes that are essentially empty after stripping templated bits

Default mode is DRY-RUN: walks every vault, prints a per-vault/per-reason report,
writes a markdown summary to `_scripts/cleaner-report-<YYYY-MM-DD>.md`.

With --apply: moves flagged files into
  <vault>/_Quarantine/auto-cleaner/<YYYY-MM-DD>/<original-rel-path>
preserving the original folder structure so the user can review and reverse.

Stdlib only. No LLM calls. Idempotent. Reversible (move files back to undo).

Run:
  python3 clean_vault.py                       # dry run, all vaults
  python3 clean_vault.py --apply               # quarantine flagged files
  python3 clean_vault.py --vault "Google Data" # one vault only
  python3 clean_vault.py --reasons marketing-promo,bot-notification
  python3 clean_vault.py --max 200             # safety cap on first apply
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import lib_vault

# ---------- Paths ----------

DEFAULT_VAULTS_ROOT = lib_vault.vaults_root()
DEFAULT_SCRIPTS_DIR = lib_vault.scripts_dir()

# ---------- Protected paths inside any vault ----------
# Anything matching these is NEVER touched, regardless of rules.

PROTECTED_DIR_NAMES = {
    "_Sources",
    "_Topics",
    "_Communities",
    "_Meta",
    "5_Meta",
    "Contacts",
    "Threads",
    "Topics",
    ".obsidian",
    "_Quarantine",     # already quarantined (idempotency)
    "_archive_chatgpt",
    "Daily",           # daily notes are always kept; they're the journal
}

PROTECTED_FILE_NAMES = {
    "_Index.md",
    "README.md",
    "MOC.md",
    "Map of Content.md",
}

# ---------- Marketing / promo patterns ----------

MARKETING_LOCAL_PARTS = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "do_not_reply",
    "mailer-daemon", "bounce", "bounces",
    "news", "newsletter", "newsletters",
    "marketing", "promo", "promos", "deals", "offers",
    "unsubscribe", "mailings", "mailing",
    "notifications", "notification",
    "info", "updates", "update",
    "team", "hello", "hi", "support",
    "automated", "auto",
}

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

# Substrings inside the from-domain that indicate bulk/transactional.
BULK_DOMAIN_SUBSTRINGS = (
    "e.uber.com", "e.lyft.com", "e.airbnb", "e.booking", "e.shopify",
    "mailer.linkedin", "mailings.linkedin",
    "email.medium.com", "email.notion.so",
    "communication.coursera", "email.udemy",
    "promotional.", "marketing.", "newsletter.", "email.",
)

UNSUBSCRIBE_RE = re.compile(r"\bunsubscribe\b", re.I)

# Gmail category labels that signal noise when the file isn't already in _Quarantine.
NOISY_GMAIL_LABELS = {
    "CATEGORY_PROMOTIONS",
    "CATEGORY_SOCIAL",
    "CATEGORY_FORUMS",
}

# ---------- Bot / automated patterns ----------

BOT_DOMAIN_SUBSTRINGS = (
    "github.com", "gitlab.com", "bitbucket",
    "linear.app", "atlassian.net", "jira.com",
    "slack.com", "slackmail.com",
    "notion.so", "figma.com",
    "loom.com",
    "circleci.com", "travis-ci", "jenkins-ci",
    "pagerduty.com", "datadoghq.com", "sentry.io",
    "stripe.com",
    "google.com",   # only paired with bot subjects below; google.com alone isn't enough
    "calendar-server.bounces.google.com",
    "facebookmail.com", "instagram.com", "twitter.com", "x.com",
    "noreply.youtube.com", "youtube.com",
    "mail.coursera.org",
)

# Subject patterns that strongly indicate a one-time auth/notification email.
OTP_PATTERNS = [
    re.compile(r"\byour (?:code|verification code|sign[- ]?in code|login code|otp)\b", re.I),
    re.compile(r"\bverification code\b", re.I),
    re.compile(r"\bone[- ]?time (?:code|password|pin)\b", re.I),
    re.compile(r"\b(?:OTP|2FA|two[- ]factor)\b"),
    re.compile(r"\bmagic link\b", re.I),
    re.compile(r"\bconfirm your email\b", re.I),
    re.compile(r"\bverify your email\b", re.I),
    re.compile(r"\bverify your account\b", re.I),
    re.compile(r"\bpassword reset\b", re.I),
    re.compile(r"\breset your password\b", re.I),
    re.compile(r"\bsign[- ]?in (?:code|link)\b", re.I),
    re.compile(r"\bpin code\b", re.I),
    re.compile(r"\bnew sign[- ]?in\b", re.I),
    re.compile(r"\baccount activation\b", re.I),
]

# Bot-style subject prefixes (with empty/short body).
BOT_SUBJECT_PREFIXES = [
    re.compile(r"^\[github\]", re.I),
    re.compile(r"^\[linear\]", re.I),
    re.compile(r"^\[gitlab\]", re.I),
    re.compile(r"^\[jira\]", re.I),
    re.compile(r"^\[slack\]", re.I),
    re.compile(r"^\[notion\]", re.I),
    re.compile(r"^\[ci\]", re.I),
    re.compile(r"^\[build\]", re.I),
]

CALENDAR_SUBJECTS = [
    re.compile(r"^invitation:", re.I),
    re.compile(r"^updated invitation:", re.I),
    re.compile(r"^canceled event:", re.I),
    re.compile(r"^cancelled event:", re.I),
    re.compile(r"^reminder:", re.I),
    re.compile(r"^accepted:", re.I),
    re.compile(r"^declined:", re.I),
]

# Transactional retail subjects that are nearly always noise.
TRANSACTIONAL_SUBJECTS = [
    re.compile(r"^your (?:order|receipt|invoice)\b", re.I),
    re.compile(r"\border confirmation\b", re.I),
    re.compile(r"\bshipping (?:update|confirmation)\b", re.I),
    re.compile(r"\bout for delivery\b", re.I),
    re.compile(r"\bdelivered\b.*\border\b", re.I),
    re.compile(r"\bpayment (?:received|confirmation)\b", re.I),
    re.compile(r"\breceipt from\b", re.I),
]

# ---------- Empty-or-near-empty ----------

EMPTY_BODY_THRESHOLD = 50          # chars after stripping
TRANSCRIPT_BODY_THRESHOLD = 30     # if frontmatter says it's a transcript

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
TAG_RE = re.compile(r"(?:^|\s)#([A-Za-z][\w/-]+)")
MARKER_BLOCK_RE = re.compile(
    r"<!--\s*\w+:start\s*-->.*?<!--\s*\w+:end\s*-->",
    re.S,
)

# ---------- Frontmatter parsing ----------

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)

EMAIL_ADDRESS_RE = re.compile(r"<?([\w._%+\-]+)@([\w.\-]+)>?")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    return lib_vault.parse_frontmatter(text)


def split_email(addr: str | None) -> tuple[str | None, str | None]:
    if not addr:
        return None, None
    m = EMAIL_ADDRESS_RE.search(addr)
    if not m:
        return None, None
    return m.group(1).lower(), m.group(2).lower()


def fm_get_labels(fm: dict) -> list[str]:
    labels = fm.get("labels") or fm.get("gmail_labels") or fm.get("categories") or []
    if isinstance(labels, str):
        # comma-separated
        return [x.strip() for x in labels.split(",") if x.strip()]
    return list(labels)


# ---------- Body cleaning for emptiness check ----------

def clean_body_for_length(body: str) -> str:
    # Drop marker blocks (auto-injected, don't count as content).
    body = MARKER_BLOCK_RE.sub("", body)
    # Drop "Open in macOS" template lines and bare "Source:" stubs.
    body = re.sub(r"^\s*\[Open in [^\]]*\]\([^)]*\)\s*$", "", body, flags=re.M)
    body = re.sub(r"^\s*Source:.*$", "", body, flags=re.M)
    # Drop pure header lines.
    lines = [l for l in body.splitlines() if l.strip() and not re.match(r"^#{1,6}\s+\S", l)]
    return "\n".join(lines).strip()


# ---------- Classifier ----------

@dataclass
class Flag:
    path: Path
    vault: str
    rel_path: Path
    reason: str          # marketing-promo | bot-notification | empty-near-empty
    evidence: str        # one-line human-readable

def is_protected(rel_path: Path) -> bool:
    parts = set(rel_path.parts)
    if parts & PROTECTED_DIR_NAMES:
        return True
    if rel_path.name in PROTECTED_FILE_NAMES:
        return True
    return False


def has_meaningful_connections(body: str) -> bool:
    """True if the connections marker block contains real wikilinks."""
    for m in re.finditer(
        r"<!--\s*connections:start\s*-->(.*?)<!--\s*connections:end\s*-->",
        body, re.S,
    ):
        if WIKILINK_RE.search(m.group(1) or ""):
            return True
    return False


def classify(path: Path, vault: str, rel_path: Path,
             enabled_reasons: set[str]) -> Flag | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    fm, body = parse_frontmatter(text)

    # Per-note overrides.
    if str(fm.get("pinned", "")).lower() == "true":
        return None
    if str(fm.get("noise", "")).lower() == "false":
        return None

    from_local, from_domain = split_email(fm.get("from") or fm.get("From") or "")
    subject = (fm.get("subject") or fm.get("Subject") or "").strip()
    labels = fm_get_labels(fm)
    kind = (fm.get("kind") or fm.get("type") or "").lower()

    # ---- Empty / near-empty rule ----
    # Defer if connections block has meaningful wikilinks (some agent already
    # linked this — assume value).
    if "empty-near-empty" in enabled_reasons and not has_meaningful_connections(body):
        cleaned = clean_body_for_length(body)
        n_links = len(WIKILINK_RE.findall(cleaned))
        n_tags = len(TAG_RE.findall(cleaned))
        threshold = TRANSCRIPT_BODY_THRESHOLD if kind in {"chat", "voicenote", "transcript"} else EMPTY_BODY_THRESHOLD
        if len(cleaned) < threshold and n_links == 0 and n_tags == 0:
            return Flag(
                path=path, vault=vault, rel_path=rel_path,
                reason="empty-near-empty",
                evidence=f"body {len(cleaned)} chars after strip, 0 links/tags",
            )

    # The rest of the rules apply primarily to email-like notes (need a from: addr).
    has_email_like_metadata = bool(from_domain) or bool(subject) or bool(labels)

    # ---- Marketing / promo rule ----
    if "marketing-promo" in enabled_reasons and has_email_like_metadata:
        # bulk-domain match
        if from_domain and (
            from_domain in BULK_DOMAINS
            or any(from_domain == d or from_domain.endswith("." + d) for d in BULK_DOMAINS)
            or any(sub in from_domain for sub in BULK_DOMAIN_SUBSTRINGS)
        ):
            return Flag(path=path, vault=vault, rel_path=rel_path,
                        reason="marketing-promo",
                        evidence=f"from-domain: {from_domain}")
        # promotional Gmail label
        if any(lbl in NOISY_GMAIL_LABELS for lbl in labels):
            return Flag(path=path, vault=vault, rel_path=rel_path,
                        reason="marketing-promo",
                        evidence=f"label: {','.join(l for l in labels if l in NOISY_GMAIL_LABELS)}")
        # marketing local-part + unsubscribe in body
        if from_local in MARKETING_LOCAL_PARTS and UNSUBSCRIBE_RE.search(body):
            return Flag(path=path, vault=vault, rel_path=rel_path,
                        reason="marketing-promo",
                        evidence=f"from: {from_local}@{from_domain}, unsubscribe in body")

    # ---- Bot / automated rule ----
    if "bot-notification" in enabled_reasons and has_email_like_metadata:
        # OTP / verification subjects
        for pat in OTP_PATTERNS:
            if pat.search(subject):
                return Flag(path=path, vault=vault, rel_path=rel_path,
                            reason="bot-notification",
                            evidence=f"subject matches OTP/verify pattern")
        # bot-domain senders
        if from_domain and any(sub in from_domain for sub in BOT_DOMAIN_SUBSTRINGS):
            # google.com alone is too broad — require bot subject prefix or short body
            if "google.com" in from_domain and not any(p.search(subject) for p in CALENDAR_SUBJECTS):
                pass
            else:
                cleaned = clean_body_for_length(body)
                if len(cleaned) < 600:  # bot mails tend to be short
                    return Flag(path=path, vault=vault, rel_path=rel_path,
                                reason="bot-notification",
                                evidence=f"from: {from_domain}, body {len(cleaned)} chars")
        # calendar invites with empty body
        if any(p.search(subject) for p in CALENDAR_SUBJECTS):
            cleaned = clean_body_for_length(body)
            if len(cleaned) < 200:
                return Flag(path=path, vault=vault, rel_path=rel_path,
                            reason="bot-notification",
                            evidence=f"calendar invite, body {len(cleaned)} chars")
        # transactional retail
        if any(p.search(subject) for p in TRANSACTIONAL_SUBJECTS):
            return Flag(path=path, vault=vault, rel_path=rel_path,
                        reason="bot-notification",
                        evidence=f"transactional subject: {subject[:60]}")
        # bot subject prefixes
        if any(p.search(subject) for p in BOT_SUBJECT_PREFIXES):
            cleaned = clean_body_for_length(body)
            if len(cleaned) < 300:
                return Flag(path=path, vault=vault, rel_path=rel_path,
                            reason="bot-notification",
                            evidence=f"bot subject prefix, body {len(cleaned)} chars")

    return None


# ---------- Walk + apply ----------

def iter_vault_notes(vault_root: Path) -> Iterable[tuple[Path, Path]]:
    """Yield (absolute_path, relative_to_vault) for every .md file under vault."""
    for p in vault_root.rglob("*.md"):
        if not p.is_file():
            continue
        rel = p.relative_to(vault_root)
        yield p, rel


def sweep_vault(vault_root: Path, vault_name: str,
                enabled_reasons: set[str]) -> list[Flag]:
    flagged: list[Flag] = []
    for path, rel in iter_vault_notes(vault_root):
        if is_protected(rel):
            continue
        f = classify(path, vault_name, rel, enabled_reasons)
        if f:
            flagged.append(f)
    return flagged


def quarantine_destination(vault_root: Path, rel: Path, today: str) -> Path:
    return vault_root / "_Quarantine" / "auto-cleaner" / today / rel


def apply_moves(flags: list[Flag], vaults_root: Path, today: str,
                max_moves: int | None) -> tuple[int, list[tuple[Flag, Path]]]:
    moved: list[tuple[Flag, Path]] = []
    for f in flags:
        if max_moves is not None and len(moved) >= max_moves:
            break
        vault_root = vaults_root / f.vault
        dest = quarantine_destination(vault_root, f.rel_path, today)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            # idempotency — already moved on a previous run
            continue
        try:
            shutil.move(str(f.path), str(dest))
            moved.append((f, dest))
        except Exception as e:
            print(f"  ! move failed: {f.path} -> {dest}: {e}", file=sys.stderr)
    return len(moved), moved


def write_manifest(vault_root: Path, today: str,
                   moves: list[tuple[Flag, Path]]) -> Path | None:
    if not moves:
        return None
    manifest_dir = vault_root / "_Quarantine" / "auto-cleaner" / today
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = manifest_dir / "_manifest.csv"
    write_header = not manifest.exists()
    with manifest.open("a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if write_header:
            w.writerow(["original_rel_path", "reason", "evidence", "quarantined_at"])
        ts = dt.datetime.now().isoformat(timespec="seconds")
        for flag, dest in moves:
            w.writerow([str(flag.rel_path), flag.reason, flag.evidence, ts])
    return manifest


# ---------- Reporting ----------

def render_report(flags_by_vault: dict[str, list[Flag]],
                  total_notes_by_vault: dict[str, int],
                  today: str, applied: bool) -> str:
    out: list[str] = []
    title = "APPLIED" if applied else "DRY RUN"
    out.append(f"# Second Brain Vault Cleaner — {title}")
    out.append(f"_Run: {today}_")
    out.append("")
    grand_total = 0
    for vault in sorted(flags_by_vault):
        flags = flags_by_vault[vault]
        if not flags:
            continue
        grand_total += len(flags)
        total = total_notes_by_vault.get(vault, 0)
        pct = (len(flags) / total * 100) if total else 0.0
        by_reason: dict[str, list[Flag]] = {}
        for f in flags:
            by_reason.setdefault(f.reason, []).append(f)
        out.append(f"## Vault: {vault}")
        out.append(f"- **Total flagged:** {len(flags)} / {total} ({pct:.1f}%)")
        for reason in ("marketing-promo", "bot-notification", "empty-near-empty"):
            items = by_reason.get(reason, [])
            if not items:
                continue
            out.append(f"- **{reason}:** {len(items)} notes")
            for ex in items[:5]:
                out.append(f"  - `{ex.rel_path}` — {ex.evidence}")
            if len(items) > 5:
                out.append(f"  - …and {len(items) - 5} more")
        out.append("")
    out.append(f"**Grand total flagged:** {grand_total}")
    if not applied:
        out.append("")
        out.append("Run again with `--apply` to move flagged files into "
                   "`<vault>/_Quarantine/auto-cleaner/{}/`.".format(today))
    return "\n".join(out) + "\n"


# ---------- Main ----------

ALL_REASONS = ("marketing-promo", "bot-notification", "empty-near-empty")

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="Move flagged files. Default is dry-run.")
    parser.add_argument("--vaults-root", default=str(DEFAULT_VAULTS_ROOT),
                        help=f"Root containing all vault folders (default: {DEFAULT_VAULTS_ROOT}).")
    parser.add_argument("--vault", action="append", default=[],
                        help="Limit to one vault (folder name under Vaults/). Repeatable.")
    parser.add_argument("--reasons", default=",".join(ALL_REASONS),
                        help="Comma-separated reasons to enable. Default: all.")
    parser.add_argument("--max", type=int, default=None,
                        help="Stop after N moves (only matters with --apply).")
    parser.add_argument("--report-dir", default=str(DEFAULT_SCRIPTS_DIR),
                        help="Where to write the markdown report.")
    args = parser.parse_args(argv)

    vaults_root = Path(args.vaults_root)
    if not vaults_root.exists():
        print(f"vaults root not found: {vaults_root}", file=sys.stderr)
        return 2

    enabled_reasons = {r.strip() for r in args.reasons.split(",") if r.strip()}
    bad = enabled_reasons - set(ALL_REASONS)
    if bad:
        print(f"unknown reason(s): {bad}. Allowed: {ALL_REASONS}", file=sys.stderr)
        return 2

    if args.vault:
        vault_dirs = [vaults_root / v for v in args.vault]
    else:
        vault_dirs = [p for p in sorted(vaults_root.iterdir())
                      if p.is_dir() and not p.name.startswith(".") and not p.name.startswith("_")]

    today = dt.date.today().isoformat()

    flags_by_vault: dict[str, list[Flag]] = {}
    total_by_vault: dict[str, int] = {}
    for vault_root in vault_dirs:
        if not vault_root.exists():
            print(f"  ! vault not found: {vault_root}", file=sys.stderr)
            continue
        # rough total note count for percentages
        total = sum(1 for _ in vault_root.rglob("*.md"))
        total_by_vault[vault_root.name] = total
        flags = sweep_vault(vault_root, vault_root.name, enabled_reasons)
        flags_by_vault[vault_root.name] = flags

    # Render dry-run report first.
    report = render_report(flags_by_vault, total_by_vault, today, applied=False)
    print(report)

    # Always write the report (even on dry-run) to disk for browsing in Obsidian.
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"cleaner-report-{today}.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"Report written: {report_path}")

    if not args.apply:
        return 0

    # APPLY phase.
    print()
    print("=== APPLYING ===")
    grand_moved = 0
    remaining_budget = args.max
    for vault_name, flags in flags_by_vault.items():
        if not flags:
            continue
        vault_root = vaults_root / vault_name
        per_vault_max = remaining_budget if remaining_budget is not None else None
        n_moved, moves = apply_moves(flags, vaults_root, today, per_vault_max)
        write_manifest(vault_root, today, moves)
        grand_moved += n_moved
        if remaining_budget is not None:
            remaining_budget -= n_moved
            if remaining_budget <= 0:
                print(f"  hit --max cap of {args.max}, stopping")
                break
        print(f"  {vault_name}: moved {n_moved}/{len(flags)}")

    # Re-render report as APPLIED summary.
    applied_report = render_report(flags_by_vault, total_by_vault, today, applied=True)
    applied_path = report_dir / f"cleaner-report-{today}-applied.md"
    applied_path.write_text(applied_report, encoding="utf-8")
    print(f"Applied report: {applied_path}")
    print(f"Total moved: {grand_moved}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
