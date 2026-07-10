#!/usr/bin/env python3
"""curate_google_drive.py — Curate the Google Drive "My Drive" folder into
Vaults/google drive/, keeping every raw_path pointed at the real macOS Google
Drive location.

Why this wrapper exists
-----------------------
auto_curate_folder.py is built for folders that live *inside* the Second Brain
(RAW /<folder>/). Its path-translation only rewrites the `/sessions/<s>/mnt/
Second Brain` sandbox prefix back to the Mac host path. The Google Drive folder
lives somewhere completely different:

    /path/to/your/google-drive

and is mounted in the Cowork sandbox at /sessions/<s>/mnt/My Drive. So we reuse
all of the curator's extraction/indexing logic but monkeypatch to_macos_path so
the source stubs' raw_path / raw_path_uri resolve on the Mac.

Idempotent — safe to re-run. This is what the daily "Google Drive vault sync"
scheduled task calls.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import auto_curate_folder as acf  # noqa: E402
import lib_vault  # noqa: E402

# Real macOS location of the Google Drive "My Drive" root.
DRIVE_HOST = "/path/to/your/google-drive"
VAULT_NAME = "google drive"


def find_drive_mount() -> Path:
    """Return the readable path to My Drive: host path if present, else the
    current Cowork sandbox mount. Session names are ephemeral, so we discover
    the mount at runtime rather than hardcoding it."""
    if Path(DRIVE_HOST).exists():
        return Path(DRIVE_HOST)
    sessions = Path("/sessions")
    if sessions.exists():
        for cand in sorted(sessions.glob("*/mnt/My Drive")):
            if cand.exists():
                return cand
    raise SystemExit("Could not locate the Google Drive 'My Drive' mount.")


# --- Patch path translation so raw_path points at the real Google Drive path,
#     regardless of which sandbox session we happen to run in. ---
_DRIVE_SANDBOX_RE = re.compile(r"^/sessions/[^/]+/mnt/My Drive")
_orig_to_macos = acf.to_macos_path


def to_macos_path(p, raw_root_macos=None):  # noqa: ANN001
    s = str(p)
    if _DRIVE_SANDBOX_RE.match(s):
        return _DRIVE_SANDBOX_RE.sub(DRIVE_HOST, s)
    # Fall back to the curator's own Second Brain translation for anything else.
    return _orig_to_macos(p, raw_root_macos)


acf.to_macos_path = to_macos_path


def main() -> None:
    drive_mount = find_drive_mount()          # .../My Drive  (host or sandbox)
    raw_parent = drive_mount.parent            # parent dir that CONTAINS "My Drive"
    vaults = lib_vault.vaults_root()           # Second Brain/Vaults (host or sandbox)

    argv = [
        "auto_curate_folder.py",
        "My Drive",                            # folder name under raw_parent
        "--raw", str(raw_parent),
        "--vaults", str(vaults),
        "--vault-name", VAULT_NAME,
        # OCR stays ON: scanned PDFs (immigration docs, receipts) become
        # searchable. It's idempotent — only files without a Note get OCR'd,
        # so the daily run stays cheap after the first pass. Large PDFs (>8MB)
        # are still skipped by the curator's own guard so a run never stalls.
    ]
    # Preserve any extra flags passed on the command line (e.g. --overwrite-notes).
    argv.extend(sys.argv[1:])
    sys.argv = argv

    print(f"[curate_google_drive] Drive source : {drive_mount}")
    print(f"[curate_google_drive] raw_path host : {DRIVE_HOST}")
    print(f"[curate_google_drive] Vault target  : {vaults / VAULT_NAME}")
    print()
    acf.main()


if __name__ == "__main__":
    main()
