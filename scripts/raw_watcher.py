#!/usr/bin/env python3
"""
raw_watcher.py — scan RAW/ for folders that haven't been curated yet, and
dispatch each unprocessed entry to the right curator.

Routing rules (in priority order):
  • Folder named "Whatsapp"        → _scripts/whatsapp_sync.py
  • Folder named "Google Takeout"  → handled by Vaults/Google Data/convert.py
                                     (mark as "manual — re-run convert.py")
  • Folder named "Claude"          → _scripts/process_claude_export.py
                                     (if present), else generic curator
  • Any other folder               → _scripts/auto_curate_folder.py "<name>"
  • Loose files at RAW root        → grouped into a synthetic "_RAW Loose Files"
                                     vault, processed via auto_curate_folder.py

Detection rules:
  • A RAW folder is "processed" if a sibling Vaults/<name>/ exists (the standard
    convention) OR if a sibling vault has been registered as its destination
    (special-case map below).
  • A processed folder is "stale" if any RAW file under it is newer than the
    newest .md inside the corresponding vault.

Default mode is dry-run — prints a plan, dispatches nothing. Pass --apply to
execute. Pass --only NAME to limit to one folder.

Usage:
    python3 raw_watcher.py
    python3 raw_watcher.py --apply
    python3 raw_watcher.py --only "New Folder"
    python3 raw_watcher.py --raw "/path/RAW " --vaults "/path/Vaults"
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

import lib_vault

DEFAULT_RAW = lib_vault.raw_root()
DEFAULT_VAULTS = lib_vault.vaults_root()
SCRIPTS_DIR = lib_vault.scripts_dir()
LOOSE_VAULT_NAME = "_RAW Loose Files"

# Map RAW folder name → vault directory name (when they differ).
RAW_TO_VAULT: dict[str, str] = {
    "ChatGPT DATA RAW": "ChatGPT",
    "Google Takeout":   "Google Data",
    "Bret and Rachel":  "DreamWorks",  # absorbed into DreamWorks
    "Tactiq":           "Personal/Tactiq",  # filed under Personal vault
    "Personal Legal":   "Personal/Legal",   # filed under Personal vault
}

# Folders that need a special curator instead of auto_curate_folder.py.
SPECIAL_CURATORS: dict[str, list[str]] = {
    "Whatsapp":        ["python3", str(SCRIPTS_DIR / "whatsapp_sync.py")],
    "Google Takeout":  ["__manual__",
                        f"Re-run Vaults/Google Data/convert.py to refresh."],
    "Claude":          ["python3", str(SCRIPTS_DIR / "process_claude_export.py")],
}


def is_skip_entry(name: str) -> bool:
    return name.startswith(".") or name == ".DS_Store"


def latest_mtime(path: Path) -> float:
    """Recursive max mtime across all files under path."""
    if path.is_file():
        return path.stat().st_mtime
    latest = 0.0
    try:
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    m = (Path(root) / f).stat().st_mtime
                    if m > latest:
                        latest = m
                except Exception:
                    continue
    except Exception:
        pass
    return latest


def vault_path_for(name: str, vaults: Path) -> Path:
    target = RAW_TO_VAULT.get(name, name).strip()  # strip trailing whitespace
    p = vaults / target
    if p.exists():
        return p
    # Fall back: try the literal (with trailing space) just in case
    return vaults / RAW_TO_VAULT.get(name, name)


def classify_folder(raw_dir: Path, vaults: Path) -> dict:
    """Return {status, vault_path, latest_raw, latest_vault, reason}."""
    name = raw_dir.name
    vault = vault_path_for(name, vaults)
    latest_raw = latest_mtime(raw_dir)
    if not vault.exists():
        return {
            "name": name, "vault": vault, "status": "new",
            "latest_raw": latest_raw, "latest_vault": 0.0,
            "reason": f"no vault at {vault.name}/",
        }
    # find newest .md in the vault
    latest_vault = 0.0
    try:
        for root, _, files in os.walk(vault):
            for f in files:
                if f.endswith(".md"):
                    m = (Path(root) / f).stat().st_mtime
                    if m > latest_vault:
                        latest_vault = m
    except Exception:
        pass
    if latest_raw > latest_vault + 60:  # 60s grace for clock skew
        return {
            "name": name, "vault": vault, "status": "stale",
            "latest_raw": latest_raw, "latest_vault": latest_vault,
            "reason": f"RAW newer than vault by {int(latest_raw - latest_vault)}s",
        }
    return {
        "name": name, "vault": vault, "status": "processed",
        "latest_raw": latest_raw, "latest_vault": latest_vault,
        "reason": "vault is up-to-date",
    }


def plan_for(entry: dict) -> dict:
    """Return {action, command|note} for a folder entry."""
    name = entry["name"]
    if entry["status"] == "processed":
        return {"action": "skip", "note": entry["reason"]}
    if name in SPECIAL_CURATORS:
        cmd = SPECIAL_CURATORS[name]
        if cmd[0] == "__manual__":
            return {"action": "manual", "note": cmd[1]}
        # whatsapp_sync.py and process_claude_export.py read from a known
        # location; no folder arg needed
        return {"action": "run", "command": cmd}
    # default: auto_curate_folder.py "<name>"
    return {"action": "run",
            "command": ["python3", str(SCRIPTS_DIR / "auto_curate_folder.py"), name]}


def detect_loose_files(raw: Path) -> list[Path]:
    out = []
    for p in raw.iterdir():
        if p.is_file() and not is_skip_entry(p.name):
            out.append(p)
    return out


def status_emoji(s: str) -> str:
    return {"new": "🆕", "stale": "🔄", "processed": "✅", "manual": "✋"}.get(s, "•")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=str(DEFAULT_RAW))
    ap.add_argument("--vaults", default=str(DEFAULT_VAULTS))
    ap.add_argument("--apply", action="store_true",
                    help="Execute the plan (default is dry-run)")
    ap.add_argument("--only", help="Limit to a single RAW folder name")
    args = ap.parse_args()

    raw = Path(args.raw)
    vaults = Path(args.vaults)
    if not raw.exists():
        print(f"ERROR: RAW not found: {raw}", file=sys.stderr)
        return 2
    if not vaults.exists():
        print(f"ERROR: vaults not found: {vaults}", file=sys.stderr)
        return 2

    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    print(f"RAW:    {raw}")
    print(f"Vaults: {vaults}")
    print()

    folders = sorted([p for p in raw.iterdir()
                      if p.is_dir() and not is_skip_entry(p.name)])
    if args.only:
        folders = [p for p in folders if p.name == args.only]

    entries = [classify_folder(d, vaults) for d in folders]
    plans = [(e, plan_for(e)) for e in entries]

    # Summary
    counts = {"new": 0, "stale": 0, "processed": 0}
    for e in entries:
        counts[e["status"]] = counts.get(e["status"], 0) + 1

    print(f"Folders: {len(entries)} total · "
          f"{counts.get('new',0)} new · "
          f"{counts.get('stale',0)} stale · "
          f"{counts.get('processed',0)} processed")
    print()

    for entry, plan in plans:
        emoji = status_emoji(entry["status"])
        print(f"  {emoji} {entry['name']}")
        print(f"      status: {entry['status']} ({entry['reason']})")
        print(f"      vault:  {entry['vault'].name}/")
        if plan["action"] == "skip":
            print(f"      action: skip")
        elif plan["action"] == "manual":
            print(f"      action: MANUAL — {plan['note']}")
        elif plan["action"] == "run":
            cmd_str = " ".join(shlex.quote(c) for c in plan["command"])
            print(f"      action: {'run' if args.apply else 'would run'}: {cmd_str}")
            if args.apply:
                try:
                    res = subprocess.run(plan["command"], capture_output=True, text=True)
                    if res.returncode == 0:
                        print(f"      ✓ exit 0")
                        # Show last line of stdout for context
                        if res.stdout.strip():
                            tail = res.stdout.strip().splitlines()[-1]
                            print(f"        {tail}")
                    else:
                        print(f"      ✗ exit {res.returncode}")
                        if res.stderr:
                            print(f"        stderr: {res.stderr.strip().splitlines()[0]}")
                except Exception as e:
                    print(f"      ✗ exception: {e}")
        print()

    # Loose files at RAW root
    loose = detect_loose_files(raw)
    if loose and not args.only:
        print(f"Loose files at RAW root: {len(loose)}")
        for p in loose:
            print(f"  • {p.name} ({p.stat().st_size} bytes)")
        print()
        print("  Loose files are not auto-curated. Move them into a folder")
        print(f"  (e.g. RAW/Misc/) and re-run, or curate explicitly with:")
        print(f"  python3 {SCRIPTS_DIR / 'auto_curate_folder.py'} <new-folder-name>")

    return 0


if __name__ == "__main__":
    sys.exit(main())
