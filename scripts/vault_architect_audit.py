#!/usr/bin/env python3
"""vault_architect_audit.py — mechanical PKM architecture audit for the
Second Brain.

Outputs a single JSON digest to `_scripts/.architect-digest-<YYYY-MM-DD>.json`
that the `second-brain-architect` skill (Claude as synthesizer) reads to write
brutal, file-grounded findings.

Three pillars audited:

  1. Graph health
       * orphan notes (no in- and no out-links)
       * hub notes (top in-degree)
       * broken wikilinks (target doesn't exist anywhere case-insensitively)
       * link density (avg / median / p95 outgoing links per note)
       * cross-vault bridges (wikilinks that resolve only in another vault)
       * MOC / _Index entry-point presence + outbound count

  2. AI-readability
       * frontmatter coverage (any frontmatter / `type:` / `kind:`)
       * _Sources stub completeness (raw_path present + resolves on disk)
       * marker-block usage (<!-- connections:start --> / <!-- sources:start -->)
       * filename collisions when lowercased (case-insensitive FS hazard)
       * near-empty notes (< 80 chars body) — AI can't ground anything
       * navigability: README + _Index per vault

  3. Code & scripts quality
       * size in lines, function/class counts, doc-string presence
       * shared-helper usage (any `from lib_` or local helper imports)
       * idempotency hints in script body (keywords: idempotent, exists, skip)
       * obvious smells: hardcoded path count, scripts > 800 lines

The digest is rule-based. NO LLM calls. Stdlib only.

Run:
  python3 vault_architect_audit.py                        # default
  python3 vault_architect_audit.py --vaults-root /path
  python3 vault_architect_audit.py --out /tmp/digest.json
  python3 vault_architect_audit.py --sample-size 30       # how many sample paths to expose for synthesis
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import lib_vault

DEFAULT_ROOT = lib_vault.base_dir()
DEFAULT_VAULTS_ROOT = DEFAULT_ROOT / "Vaults"
DEFAULT_SCRIPTS_DIR = DEFAULT_ROOT / "_scripts"

WIKILINK_RE = re.compile(r"\[\[([^|\]\n]+?)(?:\|[^\]]+)?\]\]")
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)
MARKER_CONN_RE = re.compile(r"<!--\s*connections:start\s*-->", re.I)
MARKER_SRC_RE = re.compile(r"<!--\s*sources:start\s*-->", re.I)
MARKER_CROSS_RE = re.compile(r"<!--\s*crosslink:start\s*-->", re.I)

PROTECTED_DIRS = {".obsidian", "_Quarantine", "_archive_chatgpt", "_Archive"}
EXCLUDED_VAULT_DIRS = {"_archive_chatgpt", "_Archive"}

NEAR_EMPTY_THRESHOLD = 80      # chars after stripping FM + markers
HUB_TOP_N = 25
ORPHAN_SAMPLE = 25
BROKEN_LINK_SAMPLE = 25
SCRIPT_LARGE_LINES = 800
HARDCODED_PATH_RE = re.compile(r"/Users/[^\s\"']+|/Volumes/[^\s\"']+|/private/var/[^\s\"']+")


# ---------- helpers ----------

def parse_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm_text, body = m.group(1), text[m.end():]
    fm: dict = {}
    current_key: str | None = None
    for raw in fm_text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("  -") or raw.startswith("- "):
            if current_key:
                fm.setdefault(current_key, []).append(raw.split("-", 1)[1].strip())
            continue
        if ":" in raw:
            k, _, v = raw.partition(":")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            current_key = k
            fm[k] = v if v else []
    return fm, body


def strip_for_emptiness(text: str) -> str:
    """Strip frontmatter, marker blocks, common templated lines for emptiness check."""
    _, body = parse_frontmatter(text)
    body = re.sub(r"<!--\s*\w+:start\s*-->.*?<!--\s*\w+:end\s*-->", "", body, flags=re.S)
    body = re.sub(r"\[Open in macOS\][^\n]*", "", body)
    body = re.sub(r"\[\[YYYY-MM-DD\]\]", "", body)
    return body.strip()


def safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return ""


def is_protected(rel_parts: tuple[str, ...]) -> bool:
    return any(part in PROTECTED_DIRS for part in rel_parts)


# ---------- per-vault gather ----------

def gather_vault(vault_root: Path) -> dict[str, Any]:
    name = vault_root.name
    notes_paths: list[Path] = []
    for p in vault_root.rglob("*.md"):
        try:
            rel = p.relative_to(vault_root)
        except ValueError:
            continue
        if is_protected(rel.parts):
            continue
        notes_paths.append(p)

    n_notes = len(notes_paths)

    # global indexes
    title_to_paths: dict[str, list[Path]] = defaultdict(list)        # lowercased stem -> paths
    out_links: dict[Path, list[str]] = {}
    in_degree: Counter[str] = Counter()                              # lowercased stem -> count
    fm_coverage = {"any": 0, "type": 0, "kind": 0}
    marker_conn = 0
    marker_src = 0
    marker_cross = 0
    near_empty: list[str] = []
    body_lengths: list[int] = []
    by_dir: Counter[str] = Counter()

    # _Sources audit
    source_stubs: list[Path] = []
    sources_with_raw_path = 0
    sources_resolves = 0
    sources_missing_raw: list[str] = []
    sources_unresolved: list[tuple[str, str]] = []   # (rel stub path, raw_path)

    for path in notes_paths:
        rel = path.relative_to(vault_root)
        stem_lower = path.stem.lower()
        title_to_paths[stem_lower].append(path)
        top = rel.parts[0] if len(rel.parts) > 1 else "(root)"
        by_dir[top] += 1

        text = safe_read(path)
        fm, _ = parse_frontmatter(text)
        if fm:
            fm_coverage["any"] += 1
            if "type" in fm:
                fm_coverage["type"] += 1
            if "kind" in fm:
                fm_coverage["kind"] += 1

        if MARKER_CONN_RE.search(text):
            marker_conn += 1
        if MARKER_SRC_RE.search(text):
            marker_src += 1
        if MARKER_CROSS_RE.search(text):
            marker_cross += 1

        body = strip_for_emptiness(text)
        body_lengths.append(len(body))
        if len(body) < NEAR_EMPTY_THRESHOLD and "Daily" not in rel.parts:
            near_empty.append(str(rel))

        # outgoing wikilinks
        out = []
        for m in WIKILINK_RE.finditer(text):
            target = m.group(1).strip()
            # Strip subpath/heading anchor
            target = target.split("#", 1)[0].split("/")[-1].strip()
            if not target:
                continue
            out.append(target)
        out_links[path] = out
        for t in out:
            in_degree[t.lower()] += 1

        # _Sources audit
        if rel.parts and rel.parts[0] == "_Sources":
            source_stubs.append(path)
            raw_path = (fm.get("raw_path") or "").strip().strip('"').strip("'")
            if raw_path:
                sources_with_raw_path += 1
                try:
                    resolved = Path(raw_path).exists()
                except (OSError, PermissionError):
                    resolved = False
                if resolved:
                    sources_resolves += 1
                else:
                    sources_unresolved.append((str(rel), raw_path))
            else:
                sources_missing_raw.append(str(rel))

    # Resolve broken wikilinks (case-insensitive, vault-local)
    broken: list[tuple[str, str]] = []          # (source rel, target)
    out_link_count_per_note: list[int] = []
    orphans: list[str] = []
    cross_vault_targets: Counter[str] = Counter()  # targets that don't exist in this vault
    for path, targets in out_links.items():
        out_link_count_per_note.append(len(targets))
        for t in targets:
            if t.lower() not in title_to_paths:
                broken.append((str(path.relative_to(vault_root)), t))
                cross_vault_targets[t] += 1

    for path in notes_paths:
        rel = path.relative_to(vault_root)
        out_count = len(out_links.get(path, []))
        in_count = in_degree.get(path.stem.lower(), 0)
        # ignore _Sources stubs as orphan candidates — they're scaffolding.
        # Check every path segment, not just parts[0]: vaults-of-sub-vaults
        # (e.g. AEC, Whatsapp) nest _Sources one level down
        # ("Claude Skills for AEC/_Sources/..."), which the old parts[0]-only
        # check missed and miscounted ~1,392 source stubs as orphan rot.
        if "_Sources" in rel.parts:
            continue
        if out_count == 0 and in_count == 0:
            orphans.append(str(rel))

    # Hub notes (highest in-degree that resolve)
    hubs: list[dict] = []
    for stem, deg in in_degree.most_common(HUB_TOP_N * 3):
        if stem in title_to_paths:
            sample_path = title_to_paths[stem][0]
            hubs.append({
                "title": sample_path.stem,
                "in_degree": deg,
                "rel": str(sample_path.relative_to(vault_root)),
            })
        if len(hubs) >= HUB_TOP_N:
            break

    # Stats
    def _pct(num, den):
        return round(100.0 * num / den, 1) if den else 0.0

    out_count_stats = {
        "mean": round(statistics.mean(out_link_count_per_note), 2) if out_link_count_per_note else 0.0,
        "median": int(statistics.median(out_link_count_per_note)) if out_link_count_per_note else 0,
        "p95": int(statistics.quantiles(out_link_count_per_note, n=20)[-1]) if len(out_link_count_per_note) >= 20 else 0,
        "max": max(out_link_count_per_note) if out_link_count_per_note else 0,
    }
    body_stats = {
        "mean_chars": int(statistics.mean(body_lengths)) if body_lengths else 0,
        "median_chars": int(statistics.median(body_lengths)) if body_lengths else 0,
    }

    # Filename collision check (case-insensitive)
    collisions = []
    for stem_lower, paths in title_to_paths.items():
        if len(paths) > 1:
            distinct = sorted({str(p.relative_to(vault_root)) for p in paths})
            if len(distinct) > 1:
                collisions.append({"key": stem_lower, "paths": distinct})

    # Entry points
    has_index = (vault_root / "_Index.md").exists()
    has_readme = (vault_root / "README.md").exists()
    index_outbound = 0
    if has_index:
        index_text = safe_read(vault_root / "_Index.md")
        index_outbound = len(WIKILINK_RE.findall(index_text))

    return {
        "name": name,
        "n_notes": n_notes,
        "by_top_dir": dict(by_dir.most_common()),
        "graph": {
            "out_link_count_stats": out_count_stats,
            "n_orphans": len(orphans),
            "orphan_pct": _pct(len(orphans), n_notes),
            "sample_orphans": orphans[:ORPHAN_SAMPLE],
            "n_broken_wikilinks": len(broken),
            "sample_broken": broken[:BROKEN_LINK_SAMPLE],
            "top_unresolved_targets": cross_vault_targets.most_common(20),
            "hubs": hubs,
        },
        "ai_readability": {
            "frontmatter_any_pct": _pct(fm_coverage["any"], n_notes),
            "frontmatter_type_pct": _pct(fm_coverage["type"], n_notes),
            "frontmatter_kind_pct": _pct(fm_coverage["kind"], n_notes),
            "marker_connections_pct": _pct(marker_conn, n_notes),
            "marker_sources_pct": _pct(marker_src, n_notes),
            "marker_crosslink_pct": _pct(marker_cross, n_notes),
            "body_stats": body_stats,
            "near_empty_count": len(near_empty),
            "near_empty_pct": _pct(len(near_empty), n_notes),
            "sample_near_empty": near_empty[:15],
            "filename_collisions": collisions[:15],
            "n_filename_collisions": len(collisions),
        },
        "sources": {
            "n_stubs": len(source_stubs),
            "n_with_raw_path": sources_with_raw_path,
            "n_raw_path_resolves": sources_resolves,
            "n_raw_path_missing_field": len(sources_missing_raw),
            "n_raw_path_unresolved": len(sources_unresolved),
            "raw_path_resolve_pct": _pct(sources_resolves, max(len(source_stubs), 1)),
            "sample_unresolved": sources_unresolved[:10],
            "sample_missing_raw_path": sources_missing_raw[:10],
        },
        "entry_points": {
            "has_index": has_index,
            "has_readme": has_readme,
            "index_outbound_links": index_outbound,
        },
    }


# ---------- cross-vault gather ----------

def gather_cross_vault(per_vault: list[dict[str, Any]],
                        title_index_by_vault: dict[str, set[str]]) -> dict[str, Any]:
    """Detect inter-vault bridges: notes whose unresolved local link IS resolvable in another vault."""
    bridges_found: list[dict] = []
    cross_orphan_targets: Counter[str] = Counter()
    for vault in per_vault:
        vname = vault["name"]
        for src_rel, target in vault["graph"]["sample_broken"]:
            for other_vault, titles in title_index_by_vault.items():
                if other_vault == vname:
                    continue
                if target.lower() in titles:
                    bridges_found.append({
                        "from_vault": vname,
                        "from_note": src_rel,
                        "target": target,
                        "to_vault": other_vault,
                    })
                    cross_orphan_targets[target] += 1
                    break

    # Vault-name consistency: count differences in casing/spaces
    vault_names = [v["name"] for v in per_vault]
    case_inconsistencies = []
    seen_lower: dict[str, str] = {}
    for n in vault_names:
        if n.lower() in seen_lower and seen_lower[n.lower()] != n:
            case_inconsistencies.append((seen_lower[n.lower()], n))
        seen_lower[n.lower()] = n

    return {
        "bridges_sample": bridges_found[:25],
        "n_bridges_in_sample": len(bridges_found),
        "cross_referenced_targets": cross_orphan_targets.most_common(20),
        "vault_name_case_inconsistencies": case_inconsistencies,
        "vault_count": len(per_vault),
    }


# ---------- scripts gather ----------

def gather_scripts(scripts_dir: Path) -> dict[str, Any]:
    if not scripts_dir.exists():
        return {"present": False}
    files: list[dict] = []
    function_counts: list[int] = []
    big_scripts: list[dict] = []
    helper_users: list[str] = []
    helper_definers: list[str] = []
    no_docstring: list[str] = []
    hardcoded_path_heavy: list[dict] = []

    for p in sorted(scripts_dir.glob("*.py")):
        text = safe_read(p)
        lines = text.splitlines()
        n_lines = len(lines)
        n_funcs = len(re.findall(r"^def\s+\w+\(", text, re.M))
        n_classes = len(re.findall(r"^class\s+\w+", text, re.M))
        function_counts.append(n_funcs)
        first_doc = bool(re.search(r'^"""', text, re.M))
        if not first_doc:
            no_docstring.append(p.name)

        idempotent_hint = bool(re.search(r"\bidempotent\b|\.exists\(\)|skip\b|already\b", text, re.I))
        uses_lib = bool(re.search(r"^from\s+lib_\w+\s+import", text, re.M)) or bool(re.search(r"^import\s+lib_\w+", text, re.M))
        defines_lib = p.name.startswith("lib_") or "def " + "build_sources" in text

        if uses_lib:
            helper_users.append(p.name)
        if defines_lib:
            helper_definers.append(p.name)

        hardcoded = HARDCODED_PATH_RE.findall(text)
        if len(hardcoded) > 12:
            hardcoded_path_heavy.append({"file": p.name, "n_hardcoded_paths": len(hardcoded)})

        if n_lines > SCRIPT_LARGE_LINES:
            big_scripts.append({"file": p.name, "lines": n_lines, "funcs": n_funcs})

        files.append({
            "name": p.name,
            "lines": n_lines,
            "funcs": n_funcs,
            "classes": n_classes,
            "has_docstring": first_doc,
            "idempotent_hint": idempotent_hint,
            "uses_helper": uses_lib,
            "defines_helper": defines_lib,
            "n_hardcoded_paths": len(hardcoded),
        })

    has_readme = (scripts_dir / "README.md").exists()
    test_files = list(scripts_dir.rglob("test_*.py")) + list(scripts_dir.rglob("*_test.py"))

    return {
        "present": True,
        "n_python_files": len(files),
        "files": files,
        "big_scripts": big_scripts,
        "no_module_docstring": no_docstring,
        "helper_users": helper_users,
        "helper_definers": helper_definers,
        "hardcoded_path_heavy": hardcoded_path_heavy,
        "has_readme": has_readme,
        "n_test_files": len(test_files),
        "function_count_total": sum(function_counts),
    }


# ---------- raw audit ----------

def gather_raw(raw_root: Path) -> dict[str, Any]:
    if not raw_root.exists():
        return {"present": False, "checked_path": str(raw_root)}
    top_folders = []
    loose_files = []
    for entry in sorted(raw_root.iterdir()):
        if entry.is_dir():
            try:
                n = sum(1 for _ in entry.rglob("*") if _.is_file())
            except OSError:
                n = 0
            top_folders.append({"name": entry.name, "n_files": n})
        elif entry.is_file() and not entry.name.startswith("."):
            loose_files.append(entry.name)
    return {
        "present": True,
        "n_top_folders": len(top_folders),
        "top_folders": top_folders,
        "n_loose_files_at_root": len(loose_files),
        "sample_loose_files": loose_files[:20],
    }


# ---------- sample paths for synthesis ----------

def build_sample_notes(per_vault: list[dict[str, Any]], scripts: dict[str, Any], k: int) -> list[str]:
    samples: list[str] = []
    for v in per_vault:
        vname = v["name"]
        # entry points
        if v["entry_points"]["has_index"]:
            samples.append(f"Vaults/{vname}/_Index.md")
        # one orphan + one near-empty + one hub
        for o in v["graph"]["sample_orphans"][:2]:
            samples.append(f"Vaults/{vname}/{o}")
        for ne in v["ai_readability"]["sample_near_empty"][:1]:
            samples.append(f"Vaults/{vname}/{ne}")
        for h in v["graph"]["hubs"][:2]:
            samples.append(f"Vaults/{vname}/{h['rel']}")
        for u in v["sources"]["sample_unresolved"][:1]:
            samples.append(f"Vaults/{vname}/{u[0]}")
    # scripts: a couple of the largest
    if scripts.get("present"):
        for big in scripts.get("big_scripts", [])[:3]:
            samples.append(f"_scripts/{big['file']}")
    # de-dupe preserving order
    seen, deduped = set(), []
    for s in samples:
        if s not in seen:
            seen.add(s)
            deduped.append(s)
        if len(deduped) >= k:
            break
    return deduped


# ---------- main ----------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vaults-root", type=Path, default=DEFAULT_VAULTS_ROOT)
    parser.add_argument("--scripts-dir", type=Path, default=DEFAULT_SCRIPTS_DIR)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_ROOT / "RAW ")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--sample-size", type=int, default=30)
    args = parser.parse_args()

    today = dt.date.today().isoformat()
    out_path = args.out or (args.scripts_dir / f".architect-digest-{today}.json")

    if not args.vaults_root.exists():
        print(f"ERROR: vaults root not found: {args.vaults_root}", file=sys.stderr)
        return 1

    print(f"[architect] scanning vaults under {args.vaults_root} ...", file=sys.stderr)

    per_vault: list[dict[str, Any]] = []
    title_index_by_vault: dict[str, set[str]] = {}
    for vault_path in sorted(args.vaults_root.iterdir()):
        if not vault_path.is_dir():
            continue
        if vault_path.name in EXCLUDED_VAULT_DIRS or vault_path.name.startswith("."):
            continue
        print(f"[architect] - {vault_path.name}", file=sys.stderr)
        v = gather_vault(vault_path)
        per_vault.append(v)
        # build title index
        titles = set()
        for p in vault_path.rglob("*.md"):
            try:
                rel = p.relative_to(vault_path)
            except ValueError:
                continue
            if is_protected(rel.parts):
                continue
            titles.add(p.stem.lower())
        title_index_by_vault[vault_path.name] = titles

    cross = gather_cross_vault(per_vault, title_index_by_vault)
    scripts = gather_scripts(args.scripts_dir)
    raw = gather_raw(args.raw_root)
    samples = build_sample_notes(per_vault, scripts, args.sample_size)

    # Global rollups
    total_notes = sum(v["n_notes"] for v in per_vault)
    total_orphans = sum(v["graph"]["n_orphans"] for v in per_vault)
    total_broken = sum(v["graph"]["n_broken_wikilinks"] for v in per_vault)
    total_collisions = sum(v["ai_readability"]["n_filename_collisions"] for v in per_vault)
    avg_fm_pct = round(statistics.mean([v["ai_readability"]["frontmatter_any_pct"] for v in per_vault]), 1) if per_vault else 0.0

    digest = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "vaults_root": str(args.vaults_root),
        "scripts_dir": str(args.scripts_dir),
        "raw_root": str(args.raw_root),
        "rollup": {
            "n_vaults": len(per_vault),
            "total_notes": total_notes,
            "total_orphans": total_orphans,
            "orphan_pct_global": round(100.0 * total_orphans / max(total_notes, 1), 1),
            "total_broken_wikilinks": total_broken,
            "total_filename_collisions": total_collisions,
            "avg_frontmatter_any_pct": avg_fm_pct,
        },
        "vaults": per_vault,
        "cross_vault": cross,
        "scripts": scripts,
        "raw": raw,
        "sample_notes": samples,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(digest, indent=2, default=str), encoding="utf-8")
    print(f"\n[architect] digest written: {out_path}", file=sys.stderr)
    print(f"[architect] {total_notes} notes, {total_orphans} orphans ({digest['rollup']['orphan_pct_global']}%), {total_broken} broken links, {total_collisions} filename collisions across {len(per_vault)} vaults", file=sys.stderr)
    print(str(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
