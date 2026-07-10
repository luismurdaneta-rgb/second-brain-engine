"""lib_vault — shared helpers for all Second Brain curation scripts.

Extracted 2026-06-07 per the 2026-06-01 architecture audit:
- `parse_frontmatter()` was reimplemented 6× (blind_spots_gather, clean_vault,
  extract_action_items, morning_brief, situation_data, vault_architect_audit).
- 31 hardcoded `/path/to/your/second-brain literals across 15 scripts.

Usage from any sibling script (same directory is on sys.path when run directly):

    import lib_vault
    ROOT = lib_vault.base_dir()           # Second Brain root, host or sandbox
    VAULTS = lib_vault.vaults_root()      # <root>/Vaults
    fm, body = lib_vault.parse_frontmatter(text)

Environment override: set SECOND_BRAIN_BASE to force a root.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# The ONE place the host path literal is allowed to live.
HOST_BASE = Path("/path/to/your/second-brain")

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _safe_exists(p: Path) -> bool:
    try:
        return p.exists()
    except (PermissionError, OSError):
        return False


def base_dir() -> Path:
    """Resolve the Second Brain root: env var > host path > sandbox mount."""
    env = os.environ.get("SECOND_BRAIN_BASE")
    if env and _safe_exists(Path(env)):
        return Path(env)
    if _safe_exists(HOST_BASE):
        return HOST_BASE
    sessions_root = Path("/sessions")
    if _safe_exists(sessions_root):
        for entry in sorted(sessions_root.iterdir()):
            cand = entry / "mnt" / "Second Brain"
            if _safe_exists(cand):
                return cand
    raise FileNotFoundError(
        "Second Brain root not found. Set SECOND_BRAIN_BASE env var."
    )


def vaults_root() -> Path:
    return base_dir() / "Vaults"


def scripts_dir() -> Path:
    return base_dir() / "_scripts"


def raw_root() -> Path:
    # NB: the RAW folder name has a trailing space.
    return base_dir() / "RAW "


def canonicalize_to_host(path_str: str) -> str:
    """Rewrite a possibly-sandboxed Second Brain path to the canonical
    /path/to/your/second-brain host path (for wikilinks / frontmatter that must work
    in Obsidian on the Mac)."""
    s = str(path_str)
    marker = "/mnt/Second Brain/"
    if s.startswith("/sessions/") and marker in s:
        return str(HOST_BASE) + "/" + s.split(marker, 1)[1]
    return s


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Small YAML-ish frontmatter parser. Handles `key: value` and `key:`
    followed by `- item` list lines. Returns (frontmatter_dict, body).
    Good enough for the keys our scripts care about — not full YAML."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm_text = m.group(1)
    body = text[m.end():]
    fm: dict = {}
    current_list_key: str | None = None
    for raw in fm_text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            current_list_key = None
            continue
        if line.startswith(("- ", "  - ")) and current_list_key:
            fm.setdefault(current_list_key, []).append(
                line.lstrip(" -").strip().strip('"').strip("'")
            )
            continue
        if ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if value == "":
                current_list_key = key
                fm.setdefault(key, [])
            else:
                current_list_key = None
                if (value.startswith('"') and value.endswith('"')) or (
                    value.startswith("'") and value.endswith("'")
                ):
                    value = value[1:-1]
                elif value.startswith("[") and value.endswith("]"):
                    value = [
                        v.strip().strip('"').strip("'")
                        for v in value[1:-1].split(",")
                        if v.strip()
                    ]
                fm[key] = value
    return fm, body


def frontmatter_dict(text: str) -> dict:
    """Frontmatter only, for callers that don't need the body."""
    return parse_frontmatter(text)[0]


def strip_frontmatter(text: str) -> str:
    """Body only."""
    return parse_frontmatter(text)[1]
