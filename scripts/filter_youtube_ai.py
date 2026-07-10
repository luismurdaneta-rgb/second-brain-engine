#!/usr/bin/env python3
"""
filter_youtube_ai.py — keep AI-related YouTube watch-history files, delete the rest.

Walks `Vaults/Google Data/YouTube/Watch History/` and tests each file's H1 title
against an AI/ML/LLM keyword pattern. Files that DON'T match are deleted.

Dry-run by default. Pass --apply to actually delete.

The Google Takeout source under `RAW /Google Takeout/` is NOT touched — these
deletions are reversible by re-running `convert.py` (or running this script
again after a re-import).

Usage:
    python3 filter_youtube_ai.py                      # dry-run, prints stats
    python3 filter_youtube_ai.py --sample 30          # dry-run + show 30 sample matches
    python3 filter_youtube_ai.py --apply              # actually delete non-matches
    python3 filter_youtube_ai.py --root <path>        # custom Watch History root
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

import lib_vault

DEFAULT_ROOT = lib_vault.vaults_root() / "Google Data/YouTube/Watch History"

# Two pattern groups so we can use word-boundary on short tokens:
# 1. Short tokens — must be whole-word matches (case-insensitive).
#    AI is matched as \bAI\b (case-sensitive on the letters to avoid 'said'/'paid'/etc.;
#    we then OR with [Aa]\.[Ii]\.).
SHORT_TOKEN_PATTERNS = [
    r"\bAI\b",
    r"\bA\.I\.",
    r"\bML\b",
    r"\bLLM\b",
    r"\bLLMs\b",
    r"\bGPT\b",
    r"\bMCP\b",
    r"\bRAG\b",
    r"\bNLP\b",
    r"\bAGI\b",
    r"\bASI\b",
    r"\bTPU\b",
    r"\bGPU\b",
    r"\bIA\b",      # Spanish/Portuguese for AI — relevant to user's BIM+IA group
]

# 2. Phrases — matched with WORD BOUNDARIES on each side, case-insensitive.
# This prevents short tokens like "rag", "llama", "prompt", "claude", "agent"
# from matching inside unrelated words like "Dragon", "Llamando", "prompter".
LONG_PHRASES = [
    # multi-word phrases
    "artificial intelligence",
    "machine learning",
    "deep learning",
    "neural network",
    "neural net",
    "reinforcement learning",
    "diffusion model",
    "stable diffusion",
    "generative ai",
    "generative design",
    "prompt engineering",
    "vibe coding",
    "vibecoding",
    "fine[- ]tun",          # fine-tune, fine-tuning, fine tune (regex fragment)
    "vector database",
    "agent skill",
    "ai agent",
    "ai agents",
    "ai assistant",
    "ai tool",
    "ai workflow",
    "github copilot",
    "cursor ide",
    "cursor ai",
    "replit agent",
    "lex fridman",
    "two minute papers",
    "yannic kilcher",
    "ai explained",
    "andrej karpathy",
    "3blue1brown",
    "computerphile",
    "hugging ?face",        # huggingface OR hugging face
    "lang ?chain",          # langchain
    "langgraph",
    "llama ?index",
    "ollama",
    "anthropic skills",
    "model context protocol",
    "context window",
    "system prompt",
    "function calling",
    "tool calling",
    "tool use",
    "bim ai",
    "bim\\+ia",
    "aeco ai",
    "forma autodesk",
    "revit ai",
    "rhino ai",
    "graphify",
    "obsidian ai",
    "second brain",
    "knowledge graph",
    "rag pipeline",
    "open ?source ai",
    "open-source ai",
    # single tokens (must be standalone words)
    "transformer",
    "transformers",
    "embedding",
    "embeddings",
    "agentic",
    "claude",
    "anthropic",
    "openai",
    "chatgpt",
    "gemini",
    "llama",
    "mistral",
    "deepseek",
    "qwen",
    "perplexity",
    "midjourney",
    "dall-?e",              # dall-e or dalle
    "windsurf",
    "devin",
    "tokeniz\\w*",          # tokenize, tokenizer, tokenization
    "archistar",
    "prompt",               # whole-word — won't match 'prompter'
    # 'grok' as a single token risks matching the verb meaning, but in YouTube context
    # it's almost always xAI's product. Acceptable.
    "grok",
    # 'sora' and 'runway' are too ambiguous as plain words — leave them out unless
    # paired. Title fragments like "AI Sora demo" will still match "ai" via short tokens.
]

# Wrap each pattern in word boundaries. Note: re.escape isn't applied — we want
# the patterns to allow regex syntax (\w*, alternation in []).
long_re = re.compile(
    r"(?:" + "|".join(rf"\b(?:{p})\b" for p in LONG_PHRASES) + r")",
    re.IGNORECASE,
)
short_re = re.compile("|".join(SHORT_TOKEN_PATTERNS))   # case-sensitive


def title_of(path: Path) -> str:
    """Read the file and return its H1 title (line starting with '# ')."""
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.rstrip("\n")
                if line.startswith("# "):
                    return line[2:].strip()
    except Exception:
        return ""
    return ""


def is_ai_related(title: str) -> bool:
    if not title:
        return False
    if short_re.search(title):
        return True
    if long_re.search(title):
        return True
    return False


def walk_history(root: Path):
    if not root.exists():
        return
    for year_dir in sorted(root.iterdir()):
        if not year_dir.is_dir():
            continue
        for f in year_dir.iterdir():
            if f.is_file() and f.suffix == ".md":
                yield f


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DEFAULT_ROOT),
                    help="Watch History root folder")
    ap.add_argument("--apply", action="store_true",
                    help="Actually delete non-matches (default is dry-run)")
    ap.add_argument("--sample", type=int, default=0,
                    help="Print N sample matches and N sample non-matches")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"ERROR: root not found: {root}", file=sys.stderr)
        return 2

    kept = 0
    deleted = 0
    by_year_kept: Counter = Counter()
    by_year_total: Counter = Counter()
    samples_kept: list[tuple[Path, str]] = []
    samples_dropped: list[tuple[Path, str]] = []

    for f in walk_history(root):
        year = f.parent.name
        by_year_total[year] += 1
        title = title_of(f)
        if is_ai_related(title):
            kept += 1
            by_year_kept[year] += 1
            if len(samples_kept) < args.sample:
                samples_kept.append((f, title))
        else:
            deleted += 1
            if len(samples_dropped) < args.sample:
                samples_dropped.append((f, title))
            if args.apply:
                try:
                    f.unlink()
                except Exception as e:
                    print(f"WARN: could not delete {f}: {e}", file=sys.stderr)

    print(f"Mode: {'APPLY (deleted)' if args.apply else 'DRY-RUN (no changes)'}")
    print(f"Total scanned: {kept + deleted}")
    print(f"  Keep (AI-related): {kept}")
    print(f"  {'Deleted' if args.apply else 'Would delete'}: {deleted}")
    print()
    print("Per year:")
    for year in sorted(by_year_total):
        total = by_year_total[year]
        k = by_year_kept[year]
        print(f"  {year}: keep {k} / {total}  ({k/total*100:5.1f}% retention)")

    if args.sample:
        print()
        print(f"=== Sample of KEPT (AI-related) — {len(samples_kept)} of {kept} ===")
        for p, t in samples_kept:
            print(f"  ✓ {p.name[:11]} {t[:120]}")
        print()
        print(f"=== Sample of DROPPED — {len(samples_dropped)} of {deleted} ===")
        for p, t in samples_dropped:
            print(f"  ✗ {p.name[:11]} {t[:120]}")

    if args.apply and deleted > 0:
        # Clean up any year folders that are now empty
        for year_dir in root.iterdir():
            if year_dir.is_dir() and not any(year_dir.iterdir()):
                try:
                    year_dir.rmdir()
                except Exception:
                    pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
