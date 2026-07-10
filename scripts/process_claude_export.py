#!/usr/bin/env python3
"""Process the Anthropic data export at RAW/Claude/ into Vaults/Claude/.

Output:
  Vaults/Claude/_Sources/<file>.md         — pointer stubs to the 4 raw JSONs
  Vaults/Claude/0_Inbox/<title>.md         — one note per conversation
  Vaults/Claude/5_Meta/Claude memories.md  — extracted memory profile
  Vaults/Claude/5_Meta/Project - <name>.md — one note per project

Idempotent: dedupes conversations by uuid, source stubs are overwritten.
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import lib_vault

def _detect_base() -> Path:
    """Auto-detect the Second Brain mount: native macOS path first, then any
    current sandbox mount. Avoids the dead-sandbox-path foot-gun.
    Skips sessions we can't stat (Permission denied is normal in sandbox)."""
    native = lib_vault.HOST_BASE
    try:
        if native.exists():
            return native
    except (PermissionError, OSError):
        pass
    sessions = Path("/sessions")
    try:
        session_dirs = list(sessions.iterdir()) if sessions.exists() else []
    except (PermissionError, OSError):
        session_dirs = []
    for s in session_dirs:
        cand = s / "mnt" / "Second Brain"
        try:
            if cand.exists():
                return cand
        except (PermissionError, OSError):
            continue
    raise SystemExit("ERROR: could not locate Second Brain mount")


BASE = _detect_base()
RAW = BASE / "RAW " / "Claude"
VAULT = BASE / "Vaults" / "Claude"
SOURCES = VAULT / "_Sources"
INBOX = VAULT / "0_Inbox"
META = VAULT / "5_Meta"

for d in (SOURCES, INBOX, META):
    d.mkdir(parents=True, exist_ok=True)


def macos_path(p: Path) -> str:
    s = str(p)
    # Normalize any /sessions/<name>/mnt/Second Brain/ prefix to the macOS path.
    if s.startswith("/sessions/"):
        parts = s.split("/", 5)  # ['', 'sessions', '<name>', 'mnt', 'Second Brain', '<rest>']
        if len(parts) >= 6 and parts[3] == "mnt" and parts[4] == "Second Brain":
            s = str(lib_vault.HOST_BASE) + "/" + parts[5]
    return s


def file_uri(p: str) -> str:
    return "file://" + quote(p, safe="/:")


def computer_uri(p: str) -> str:
    return "computer://" + quote(p, safe="/:")


def safe(name: str, n: int = 100) -> str:
    s = re.sub(r'[/\\:*?"<>|]', "-", name)
    s = re.sub(r"\s+", " ", s).replace("\n", " ").strip().strip(".")
    return (s or "Untitled")[:n]


def find_existing_session(session_id: str) -> Path | None:
    """Mirror archive_conversation.py's lookup — dedupe by session_id."""
    if not session_id:
        return None
    needle = f"session_id: {session_id}"
    for md in VAULT.rglob("*.md"):
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if needle in text[:1500]:
            return md
    return None


# ---- Step 1: Build _Sources/ stubs for the 4 JSONs ----
print("[1/4] Building _Sources/ stubs...")
files_for_stubs = [
    (RAW / "conversations.json", "data", "🗂", "Anthropic export — all Claude.ai conversations"),
    (RAW / "memories.json", "data", "🧠", "Anthropic export — Claude's stored memory about the user"),
    (RAW / "users.json", "data", "👤", "Anthropic export — account metadata"),
]
for proj_json in (RAW / "projects").glob("*.json"):
    files_for_stubs.append((proj_json, "data", "📁", "Anthropic export — Claude.ai project"))

for src, kind, emoji, desc in files_for_stubs:
    if not src.exists():
        continue
    macos = macos_path(src)
    size = src.stat().st_size
    body = []
    body.append("---")
    body.append("type: source")
    body.append(f"kind: {kind}")
    body.append(f'filename: "{src.name}"')
    body.append("extension: json")
    body.append(f'raw_path: "{macos}"')
    body.append(f"raw_path_uri: {file_uri(macos)}")
    body.append(f"size_bytes: {size}")
    body.append("tags:")
    body.append("  - source")
    body.append(f"  - source/{kind}")
    body.append("  - claude-export")
    body.append("---")
    body.append("")
    body.append(f"# {emoji} {src.name}")
    body.append("")
    body.append(f"{desc}")
    body.append("")
    body.append(f"[Open in macOS]({file_uri(macos)})")
    body.append("")
    body.append(f"```\n{macos}\n```")
    (SOURCES / f"{safe(src.stem)}.md").write_text("\n".join(body), encoding="utf-8")

print(f"      Wrote {len(files_for_stubs)} source stubs")


# ---- Step 2: Parse conversations.json into per-conversation transcript notes ----
print("[2/4] Parsing conversations.json...")
with open(RAW / "conversations.json", encoding="utf-8") as f:
    conversations = json.load(f)

written = 0
skipped = 0


def render_text_block(text: str) -> str:
    """Strip nothing — this is your own conversation, no secrets to redact."""
    return text or ""


def parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


for conv in conversations:
    uuid = conv.get("uuid", "")
    if not uuid:
        continue
    if find_existing_session(uuid):
        skipped += 1
        continue

    name = conv.get("name", "") or "Untitled conversation"
    created = conv.get("created_at", "")
    updated = conv.get("updated_at", "")
    summary = (conv.get("summary") or "").strip()
    messages = conv.get("chat_messages", []) or []

    # Title: prefer name; truncate to 80 chars
    title = name.strip() or f"Conversation {uuid[:8]}"
    title = title[:120]

    # Build transcript
    transcript_lines = []
    for m in messages:
        sender = m.get("sender", "unknown")
        ts = m.get("created_at", "")
        text = (m.get("text") or "").strip()
        if not text and m.get("content"):
            # content is a list of blocks in some formats
            content = m["content"]
            if isinstance(content, list):
                parts = []
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        parts.append(c.get("text", ""))
                text = "\n\n".join(parts).strip()
        time_part = ""
        dt = parse_iso(ts)
        if dt:
            time_part = dt.strftime(" — %H:%M")

        if sender == "human":
            transcript_lines.append(f"**👤 You{time_part}**\n\n{render_text_block(text)}\n")
        elif sender in ("assistant", "claude"):
            transcript_lines.append(f"**🤖 Claude{time_part}**\n\n{render_text_block(text)}\n")
        else:
            transcript_lines.append(f"**{sender}{time_part}**\n\n{render_text_block(text)}\n")

    transcript = "\n".join(transcript_lines).strip()

    # Frontmatter
    fm = []
    fm.append("---")
    fm.append("type: claude-conversation")
    fm.append("source: claude-web")
    fm.append(f"session_id: {uuid}")
    fm.append(f'title: "{title.replace(chr(34), chr(39))}"')
    fm.append(f"created: {created}")
    fm.append(f"last_message: {updated}")
    fm.append("model: unknown")
    fm.append(f"message_count: {len(messages)}")
    fm.append("tool_call_count: 0")
    fm.append("para: inbox")
    fm.append('topic: ""')
    fm.append("tags:")
    fm.append("  - claude-conversation")
    fm.append("  - claude-web")
    fm.append("---")

    body = []
    body.append(f"# {title}")
    body.append("")
    if summary:
        body.append("## Summary")
        body.append("")
        body.append(summary[:2000])
        body.append("")
    body.append("## Transcript")
    body.append("")
    body.append(transcript)
    body.append("")
    body.append("<!-- connections:start -->")
    body.append("## Connections")
    body.append("")
    if created:
        date_stem = created[:10]
        body.append(f"- **Daily note:** [[{date_stem}]]")
    body.append("- **Source:** [[conversations]] (claude-export)")
    body.append("- **Topic:** _(triage from `0_Inbox/` and fill in)_")
    body.append("<!-- connections:end -->")

    fname = safe(title)
    out = INBOX / f"{fname}.md"
    if out.exists():
        out = INBOX / f"{fname} ({uuid[:8]}).md"
    out.write_text("\n".join(fm) + "\n" + "\n".join(body) + "\n", encoding="utf-8")
    written += 1

print(f"      Wrote {written} conversations, skipped {skipped} (already archived)")


# ---- Step 3: Parse memories.json + projects ----
print("[3/4] Parsing memories.json and projects/...")
with open(RAW / "memories.json", encoding="utf-8") as f:
    memories = json.load(f)

# memories is a list with a single dict containing 'conversations_memory' (and possibly more)
if isinstance(memories, list) and memories:
    mem = memories[0]
    mem_text = mem.get("conversations_memory", "")
elif isinstance(memories, dict):
    mem_text = memories.get("conversations_memory", "")
else:
    mem_text = json.dumps(memories, indent=2)

mem_body = [
    "---",
    "type: claude-memories",
    "source: claude-web",
    "tags:",
    "  - claude-memories",
    "  - meta",
    "---",
    "",
    "# Claude's stored memories",
    "",
    "Imported from Anthropic data export (`memories.json`). This is what Claude.ai has accumulated as long-term context about the user across sessions.",
    "",
    "**Source:** [[memories]] (raw JSON)",
    "",
    "## Memory contents",
    "",
    mem_text or "_(empty)_",
    "",
]
(META / "Claude memories.md").write_text("\n".join(mem_body), encoding="utf-8")

# Projects
for proj_json in (RAW / "projects").glob("*.json"):
    with open(proj_json, encoding="utf-8") as f:
        proj = json.load(f)
    name = proj.get("name", proj_json.stem)
    desc = proj.get("description", "") or ""
    created = proj.get("created_at", "")
    updated = proj.get("updated_at", "")
    docs = proj.get("docs", []) or []
    prompt_template = proj.get("prompt_template", "") or ""

    pbody = []
    pbody.append("---")
    pbody.append("type: claude-project")
    pbody.append("source: claude-web")
    pbody.append(f"project_uuid: {proj.get('uuid', '')}")
    pbody.append(f'name: "{name.replace(chr(34), chr(39))}"')
    pbody.append(f"created: {created}")
    pbody.append(f"updated: {updated}")
    pbody.append(f"docs_count: {len(docs)}")
    pbody.append("tags:")
    pbody.append("  - claude-project")
    pbody.append("  - meta")
    pbody.append("---")
    pbody.append("")
    pbody.append(f"# Project — {name}")
    pbody.append("")
    if desc:
        pbody.append("## Description")
        pbody.append("")
        pbody.append(desc)
        pbody.append("")
    if prompt_template:
        pbody.append("## Project instructions / prompt template")
        pbody.append("")
        pbody.append("```")
        pbody.append(prompt_template[:5000])
        pbody.append("```")
        pbody.append("")
    if docs:
        pbody.append(f"## Knowledge base ({len(docs)} docs)")
        pbody.append("")
        for d in docs[:20]:
            doc_name = d.get("file_name", "?") if isinstance(d, dict) else str(d)
            pbody.append(f"- {doc_name}")
        if len(docs) > 20:
            pbody.append(f"- _…and {len(docs) - 20} more_")
        pbody.append("")
    pbody.append(f"**Source:** [[{safe(proj_json.stem)}]] (raw JSON)")

    (META / f"Project - {safe(name)}.md").write_text("\n".join(pbody), encoding="utf-8")

print(f"      Wrote memories + {len(list((RAW / 'projects').glob('*.json')))} project notes")


# ---- Step 4: Refresh _Index.md ----
print("[4/4] Refreshing _Index.md with new content...")
inbox_count = sum(1 for _ in INBOX.glob("*.md"))
meta_count = sum(1 for _ in META.glob("*.md"))
sources_count = sum(1 for _ in SOURCES.glob("*.md"))

idx_body = f"""---
type: index
tags:
  - moc
  - claude
last_updated: {datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}
---
# Claude — Map of Content

Archive of conversations and metadata from both **Cowork desktop sessions** and **Claude.ai web chats**.

## Stats

- **0_Inbox/**: {inbox_count} conversations awaiting triage
- **5_Meta/**: {meta_count} metadata notes (memories, projects)
- **_Sources/**: {sources_count} pointers to RAW JSON exports

## Two streams of conversations

| Source | Where it comes from | Auto-import |
|---|---|---|
| **Cowork sessions** | This desktop app — `mcp__session_info__read_transcript` | Yes (10pm scheduled task) |
| **Claude.ai web chats** | Anthropic data export → `RAW /Claude/conversations.json` | No, manual re-import each time you re-export |

## Triage workflow

New conversations land in `0_Inbox/`. Move them into:
- `1_Projects/` — active project work
- `2_Areas/` — long-running themes
- `3_Resources/` — research, references
- `4_Archive/` — done/cold
- `5_Meta/` — vault-about-the-vault

Once moved, fill in the `topic:` frontmatter and the `**Topic:**` line in the connections block — that's how `_Topics/<topic>.md` MOCs eventually generate.

## Key meta notes

- [[Claude memories]] — Claude.ai's stored long-term memory profile of the user
- Project notes under `5_Meta/Project - <name>.md`

## Sources

- `_Sources/conversations.md` → [Anthropic export — all Claude.ai conversations]
- `_Sources/memories.md` → Claude's stored memory profile
- `_Sources/users.md` → account metadata

## Re-importing later

When you do another Anthropic data export:
1. Replace files in `RAW /Claude/`
2. Re-run the import script (`process_claude_export.py`)
3. New conversations land in `0_Inbox/`; existing ones (matched by `session_id`) are skipped — your triage stays intact.
"""

(VAULT / "_Index.md").write_text(idx_body, encoding="utf-8")

print()
print("DONE.")
print(f"  _Sources/ stubs:        {sources_count}")
print(f"  0_Inbox/ conversations: {inbox_count}")
print(f"  5_Meta/ notes:          {meta_count}")
