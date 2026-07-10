#!/usr/bin/env python3
"""build_whatsapp_contact_stubs.py — kill broken WhatsApp contact wikilinks.

Problem:
    Vaults/Whatsapp/Messages/*/*.md contains `[[Barbara Bernardo]]`,
    `[[Daniel Veludo]]`, etc. — every contact name appears as a wikilink.
    These wikilinks resolve in Vaults/Google Data/Contacts/ but NOT inside
    the WhatsApp vault (Obsidian wikilinks are vault-local). As of
    2026-06-01 this accounts for 6,822 of 10,893 broken wikilinks (63%).

Fix:
    For every unique `[[Name]]` target appearing in WhatsApp messages,
    write a thin Vaults/Whatsapp/Contacts/<Name>.md redirect stub. The
    stub resolves the local wikilink AND backlinks to the canonical
    Google Data contact (when one exists), so the contact graph stays
    centralized while the WhatsApp vault stops being a dead-link graveyard.

    For phone-number-only names (e.g. `+55 31 8202-0789`), a stub is
    still written so the wikilink resolves locally; it carries a
    `canonical: null` and `kind: phone-only` frontmatter so a future
    consolidate-contacts pass can attach a real identity.

Stdlib only. Idempotent. Dry-run by default.

Usage:
    python3 build_whatsapp_contact_stubs.py                    # dry-run report
    python3 build_whatsapp_contact_stubs.py --apply            # write stubs
    python3 build_whatsapp_contact_stubs.py --vaults /path/to/Vaults
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

import lib_vault

# ---- defaults ----
DEFAULT_VAULTS = lib_vault.vaults_root()
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")

# stems that aren't contacts — skip them (groups, topics, media files)
SKIP_PATTERNS = [
    re.compile(r"^(IMG|VID|AUD|DOC)-\d{8}-WA\d{4}", re.IGNORECASE),  # media files
    re.compile(r"\.(mp4|jpg|jpeg|png|opus|webp|gif|pdf|m4a|mp3|docx|doc|xlsx|xls|zip|rar|csv|tsv|json|txt)$", re.IGNORECASE),
]
# explicit non-contact targets seen in messages — group/topic/community names
NON_CONTACT_TARGETS = {
    "Grupo 2 - BIM + IA",
    "AECO_AI_Workflows",
    "IA para AECO",
    "BIM + IA",
    "Zigurat",
    "TFM",
    "grupo-2-bim-ia",
    "ia-para-aeco-zigurat",
    "tfm-bim-ia-grupo-2",
}

# alias map — canonical-name -> [aliases that should map to the same contact]
# the user Miguel Urdaneta (me).md is the canonical, but messages use the short form.
ALIAS_TO_CANONICAL = {
    "the user Miguel Urdaneta": "the user Miguel Urdaneta (me)",
}


def is_phone(name: str) -> bool:
    # rough: starts with + and contains digits
    return name.strip().startswith("+") and any(c.isdigit() for c in name)


def should_skip(target: str) -> bool:
    if not target.strip():
        return True
    if target in NON_CONTACT_TARGETS:
        return True
    for pat in SKIP_PATTERNS:
        if pat.search(target):
            return True
    return False


def collect_targets(messages_root: Path) -> Counter[str]:
    """Walk Whatsapp/Messages/**/*.md and count [[target]] occurrences."""
    counts: Counter[str] = Counter()
    for md in messages_root.rglob("*.md"):
        try:
            text = md.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for m in WIKILINK_RE.finditer(text):
            target = m.group(1).strip()
            if should_skip(target):
                continue
            counts[target] += 1
    return counts


def find_canonical_contact(name: str, contacts_root: Path) -> Path | None:
    """Look up a contact in Google Data/Contacts/. Returns the canonical file
    if it exists, accounting for the alias map."""
    if not contacts_root.exists():
        return None
    candidate_stems = [name]
    if name in ALIAS_TO_CANONICAL:
        candidate_stems.insert(0, ALIAS_TO_CANONICAL[name])
    # also try lowercase-insensitive match
    lower_to_path = {p.stem.lower(): p for p in contacts_root.glob("*.md")}
    for stem in candidate_stems:
        if stem.lower() in lower_to_path:
            return lower_to_path[stem.lower()]
    return None


def make_stub(name: str, canonical_path: Path | None, vaults_root: Path) -> str:
    """Build the redirect-stub markdown body."""
    phone = is_phone(name)
    if canonical_path is not None:
        # build a vault-relative wikilink — Obsidian resolves
        # `[[Google Data/Contacts/Barbara Bernardo]]` when opened as a
        # multi-folder workspace, and as a friendly fallback the stub also
        # carries the short alias so basename-resolution still works locally.
        rel = canonical_path.relative_to(vaults_root)
        # drop .md suffix
        rel_no_ext = rel.with_suffix("")
        canonical_link = f"[[{rel_no_ext.as_posix()}|{name}]]"
        kind = "contact-redirect"
        canonical_yaml = f'"{rel_no_ext.as_posix()}"'
    else:
        canonical_link = "_(no canonical contact found in Google Data)_"
        kind = "phone-only" if phone else "contact-stub"
        canonical_yaml = "null"

    body = [
        "---",
        "type: contact",
        f"kind: {kind}",
        f'name: "{name}"',
        f"canonical: {canonical_yaml}",
        "tags:",
        "  - whatsapp",
        "  - contact-stub",
        "---",
        "",
        f"# {name}",
        "",
        f"**Canonical:** {canonical_link}",
        "",
        "<!-- sources:start -->",
        "## Sources",
        "",
        "WhatsApp messages — see backlinks panel for every monthly chat bundle "
        "in `Vaults/Whatsapp/Messages/`.",
        "<!-- sources:end -->",
        "",
        "<!-- connections:start -->",
        "## Connections",
        "",
    ]
    if canonical_path is not None:
        body.append(
            "This is a local redirect so WhatsApp wikilinks resolve. The full "
            "contact record — email backlinks, threads, notes — lives in "
            f"{canonical_link}."
        )
    else:
        body.append(
            "No canonical contact found in `Google Data/Contacts/`. If you "
            "later identify this number, link it here and rename the stub."
        )
    body.append("<!-- connections:end -->")
    body.append("")
    return "\n".join(body)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vaults", type=Path, default=DEFAULT_VAULTS)
    ap.add_argument("--apply", action="store_true",
                    help="actually write files (default: dry-run report)")
    ap.add_argument("--min-occurrences", type=int, default=1,
                    help="only emit stubs for targets seen >= N times")
    args = ap.parse_args()

    vaults = args.vaults
    wa_root = vaults / "Whatsapp"
    msgs_root = wa_root / "Messages"
    contacts_root = vaults / "Google Data" / "Contacts"
    stub_dir = wa_root / "Contacts"

    if not msgs_root.exists():
        print(f"ERROR: not found: {msgs_root}", file=sys.stderr)
        return 2

    print(f"[whatsapp-stubs] scanning {msgs_root}")
    targets = collect_targets(msgs_root)
    print(f"[whatsapp-stubs] {len(targets)} distinct wikilink targets in WhatsApp messages")

    by_status = {"existing-canonical": [], "phone-only": [], "no-canonical": []}
    for name, count in targets.most_common():
        if count < args.min_occurrences:
            continue
        canonical = find_canonical_contact(name, contacts_root)
        if canonical is not None:
            by_status["existing-canonical"].append((name, count, canonical))
        elif is_phone(name):
            by_status["phone-only"].append((name, count, None))
        else:
            by_status["no-canonical"].append((name, count, None))

    print()
    print(f"  with canonical contact:  {len(by_status['existing-canonical']):4d}  "
          f"({sum(c for _,c,_ in by_status['existing-canonical'])} occurrences)")
    print(f"  phone-only:              {len(by_status['phone-only']):4d}  "
          f"({sum(c for _,c,_ in by_status['phone-only'])} occurrences)")
    print(f"  no-canonical (other):    {len(by_status['no-canonical']):4d}  "
          f"({sum(c for _,c,_ in by_status['no-canonical'])} occurrences)")
    total_occurrences = sum(c for items in by_status.values() for _, c, _ in items)
    print(f"  total broken-link kills: {total_occurrences}")
    print()

    if not args.apply:
        print("--- DRY RUN — sample of what would be written ---")
        for status, items in by_status.items():
            print(f"\n[{status}] (showing first 5)")
            for name, count, canonical in items[:5]:
                target = canonical.relative_to(vaults).as_posix() if canonical else "—"
                print(f"  Whatsapp/Contacts/{name}.md  ← {count} refs, canonical: {target}")
        print()
        print(f"Re-run with --apply to write {sum(len(v) for v in by_status.values())} stub files into {stub_dir}/")
        return 0

    # apply
    stub_dir.mkdir(parents=True, exist_ok=True)
    n_written = 0
    n_skipped_existing = 0
    for status, items in by_status.items():
        for name, count, canonical in items:
            # sanitize for filename — names already valid, but strip forbidden chars
            safe_stem = re.sub(r"[\\/:\*\?\"<>|]", "_", name).strip()
            if not safe_stem:
                continue
            target_path = stub_dir / f"{safe_stem}.md"
            if target_path.exists():
                # idempotent: only overwrite if our stub-managed header is present
                existing = target_path.read_text(encoding="utf-8", errors="ignore")
                if "kind: contact-redirect" not in existing and "kind: contact-stub" not in existing and "kind: phone-only" not in existing:
                    print(f"[skip] existing non-stub file: {target_path}")
                    n_skipped_existing += 1
                    continue
            body = make_stub(name, canonical, vaults)
            target_path.write_text(body, encoding="utf-8")
            n_written += 1

    print(f"\n[whatsapp-stubs] wrote {n_written} stubs into {stub_dir}/")
    if n_skipped_existing:
        print(f"[whatsapp-stubs] skipped {n_skipped_existing} pre-existing non-stub files")
    print(f"[whatsapp-stubs] expected broken-link drop: ~{total_occurrences} refs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
