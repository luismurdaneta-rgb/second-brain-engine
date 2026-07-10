#!/usr/bin/env python3
"""
consolidate_contacts.py — merge duplicate Contact notes that arose because
different sync sources used different display-name conventions for the same
person.

Pass `--mappings` as repeated `from=>to` pairs (case-sensitive stems, no .md).
Use `--vaults` to scope the wikilink rewrite. Always preview with `--dry-run`
first.

For each mapping `from=>to`:
  1. If both files exist, merge frontmatter (taking non-empty fields from
     either, preferring `to`'s) plus appending the body of `from` after `to`'s.
  2. Delete `from`.
  3. Walk every `.md` under `--vaults` and rewrite `[[from]]` and
     `[[from|alias]]` → `[[to]]` / `[[to|alias]]`.

Usage:
    python3 consolidate_contacts.py --dry-run \\
        --vaults "/path/to/your/second-brain/Vaults" \\
        --mappings \\
            "forneck naiara=>Naiara Forneck" \\
            "daniel v17=>Daniel Veludo" \\
            "barbarambernardo=>Barbara Bernardo" \\
            "rlimaarq=>Rogério Maicpt"
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def parse_mapping(s: str) -> tuple[str, str]:
    if "=>" not in s:
        raise argparse.ArgumentTypeError(f"bad mapping {s!r}; expected 'from=>to'")
    a, b = s.split("=>", 1)
    return a.strip(), b.strip()


def merge_files(src: Path, dst: Path, dry_run: bool) -> None:
    if not src.exists():
        return
    src_text = src.read_text(encoding="utf-8", errors="ignore")
    if dst.exists():
        dst_text = dst.read_text(encoding="utf-8", errors="ignore")
        # Append the source body (sans frontmatter) under a divider
        src_body = re.sub(r"^---\n.*?\n---\n", "", src_text, count=1, flags=re.DOTALL).strip()
        # If src has email frontmatter and dst doesn't, copy the email
        src_email_m = re.search(r"^email:\s*(\S+)\s*$", src_text, flags=re.MULTILINE)
        if src_email_m:
            src_email = src_email_m.group(1)
            if re.search(r"^email:\s*$", dst_text, flags=re.MULTILINE):
                dst_text = re.sub(r"^email:\s*$", f"email: {src_email}", dst_text, count=1, flags=re.MULTILINE)
            elif "email:" not in dst_text.split("---", 2)[1] if dst_text.startswith("---") else True:
                # add email line
                dst_text = re.sub(r"^(name:.*)$", r"\1\n" + f"email: {src_email}", dst_text, count=1, flags=re.MULTILINE)
        merged = dst_text.rstrip() + "\n\n---\n\n" + src_body + "\n"
        if dry_run:
            print(f"  [dry-run] merge {src.name} → {dst.name}")
        else:
            dst.write_text(merged, encoding="utf-8")
            src.unlink()
            print(f"  merged & deleted {src.name} → {dst.name}")
    else:
        # No destination yet — just rename
        if dry_run:
            print(f"  [dry-run] rename {src.name} → {dst.name}")
        else:
            src.rename(dst)
            print(f"  renamed {src.name} → {dst.name}")


def rewrite_wikilinks(vaults: Path, mappings: list[tuple[str, str]], dry_run: bool) -> int:
    total = 0
    for f in vaults.rglob("*.md"):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        new = text
        for src_stem, dst_stem in mappings:
            if src_stem == dst_stem:
                continue
            # [[src]] or [[src|alias]] → [[dst]] / [[dst|alias]]
            esc = re.escape(src_stem)
            new = re.sub(rf"\[\[{esc}(\|[^\]]*)?\]\]",
                         lambda m: f"[[{dst_stem}{m.group(1) or ''}]]",
                         new)
        if new != text:
            total += 1
            if not dry_run:
                f.write_text(new, encoding="utf-8")
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vaults", required=True)
    ap.add_argument("--mappings", nargs="+", type=parse_mapping, required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    vaults = Path(args.vaults)
    if not vaults.exists():
        print(f"ERROR: vaults not found: {vaults}", file=sys.stderr)
        return 2

    contacts_dir = vaults / "Google Data" / "Contacts"
    print(f"Mode: {'DRY RUN' if args.dry_run else 'APPLY'}")
    print(f"Contacts dir: {contacts_dir}")
    print()

    print("=== Merging notes ===")
    for src_stem, dst_stem in args.mappings:
        merge_files(contacts_dir / f"{src_stem}.md", contacts_dir / f"{dst_stem}.md", args.dry_run)

    print()
    print("=== Rewriting wikilinks across all vaults ===")
    n = rewrite_wikilinks(vaults, args.mappings, args.dry_run)
    print(f"  {'would rewrite' if args.dry_run else 'rewrote'} wikilinks in {n} files")

    return 0


if __name__ == "__main__":
    sys.exit(main())
