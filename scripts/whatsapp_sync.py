#!/usr/bin/env python3
"""
whatsapp_sync.py — turn WhatsApp chat exports into a curated Obsidian vault
that plugs into the rest of the Second Brain (shared Contacts/, cross-linked
communities, media-aware).

Input layout — under `RAW /Whatsapp/`:
    WhatsApp Chat with <Group Name>/
        WhatsApp Chat with <Group Name>.txt
        IMG-YYYYMMDD-WAxxxx.jpg
        VID-YYYYMMDD-WAxxxx.mp4
        ...

Output layout:
    Vaults/Whatsapp/
        _Index.md                        — vault MOC
        Chats/<group>.md                 — per-group MOC
        Messages/<group-slug>/<YYYY-MM>.md — month-bundled transcripts
        _Sources/<media-file>.md         — stub for every media file
    Vaults/Google Data/Contacts/<name>.md — a "WhatsApp messages" section is
                                            appended to existing contacts, or
                                            new ones are created.

The script is idempotent: re-running on the same RAW dump overwrites the vault
output but does not duplicate Contact-note sections (it replaces marker blocks).

Usage:
    python3 whatsapp_sync.py
    python3 whatsapp_sync.py --raw "/path/to/RAW /Whatsapp"
    python3 whatsapp_sync.py --vault "/path/to/Vaults"
    python3 whatsapp_sync.py --dry-run
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import lib_vault

DEFAULT_RAW = lib_vault.raw_root() / "Whatsapp"
DEFAULT_VAULTS = lib_vault.vaults_root()

# Canonical host path for the vault root — every `raw_path:` we persist must
# use this prefix so frontmatter resolves on the user's Mac, not inside a
# Cowork sandbox that disappears between sessions.
HOST_VAULT_ROOT = str(lib_vault.HOST_BASE)
_SANDBOX_PREFIX_RE = re.compile(r"^/sessions/[^/]+/mnt/Second Brain")


def canonicalize_to_host(p) -> str:
    """Rewrite a possibly-sandboxed path to the canonical /path/to/your/second-brain path.

    Cowork mounts the vault under /sessions/<session>/mnt/Second Brain/. If a
    script run inside a sandbox writes that literal into frontmatter, the link
    is dead the moment the session ends. Always canonicalize before persisting.
    """
    s = str(p)
    return _SANDBOX_PREFIX_RE.sub(HOST_VAULT_ROOT, s)

ME = "the user Miguel Urdaneta"
ME_DISPLAY_FOR_VAULT = "the user Miguel Urdaneta (me)"

# Map phone numbers (raw form from chat) to known display names.
# Built up by inspecting the TFM event attendees. Editable.
PHONE_ALIAS_MAP: dict[str, str] = {
    "+351 964 035 072": "Barbara Bernardo",
    "+55 31 8813-1398": "Naiara Forneck",  # provisional — confirm
    "+55 51 9510-5191": "Naiara Forneck",  # provisional — confirm
}

# Topic / community wikilinks per chat folder. The match is substring
# (case-insensitive) against the folder's group name.
GROUP_TOPIC_LINKS: list[tuple[str, list[str]]] = [
    ("Grupo 2 - BIM + IA",     ["Grupo 2 - BIM + IA",     "BIM + IA",  "AECO_AI_Workflows"]),
    ("IA para AECO",           ["AECO_AI_Workflows",      "Zigurat",   "IA para AECO"]),
    ("TFM - BIM + IA",         ["TFM",                     "Grupo 2 - BIM + IA"]),
]

WA_BEGIN = "<!-- whatsapp:start -->"
WA_END = "<!-- whatsapp:end -->"

# ── Parsing ────────────────────────────────────────────────────────────────────

# Date prefixes look like (two formats — older WhatsApp exports use the first,
# newer iOS/EU exports use the second bracketed form with optional U+200E LRM):
#   3/2/26, 10:05 AM - <rest>
#   [22/05/2026, 18:49:55] <rest>
DATE_LINE_RE = re.compile(
    r"^(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y>\d{2,4}),\s+(?P<h>\d{1,2}):(?P<min>\d{2})\s*(?P<ampm>AM|PM)\s+-\s+(?P<rest>.*)$"
)
DATE_LINE_RE_BRACKET = re.compile(
    r"^‎?\[(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>\d{2,4}),\s+(?P<h>\d{1,2}):(?P<min>\d{2})(?::(?P<sec>\d{2}))?\]\s*(?P<rest>.*)$"
)
# A normal message: "Sender: body"
SENDER_BODY_RE = re.compile(r"^(?P<sender>[^:]{1,80}?):\s+(?P<body>.*)$")
# System event: "~ X did Y" (no colon, no real sender)
SYSTEM_LINE_RE = re.compile(r"^~?\s*([^:]+ (?:created|added|removed|changed|left|joined|pinned|unpinned).+)$")

MENTION_RE = re.compile(r"@⁨([^⁩]+)⁩")          # WhatsApp mention markers
EDITED_TAG_RE = re.compile(r"\s*<This message was edited>\s*$")
MEDIA_OMITTED = "<Media omitted>"
ATTACH_RE = re.compile(r"^(?P<file>[\w\.\-+]+) \(file attached\)\s*$")
# Newer export attachment marker: "<attached: filename.ext>" (with optional LRM)
ATTACH_BRACKET_RE = re.compile(r"^‎?<attached:\s*(?P<file>[^>]+)>\s*$")


@dataclass
class Message:
    dt: datetime
    sender_raw: str         # exact text from the chat
    sender_norm: str        # human-readable name (mapped where possible)
    body: str
    media_files: list[str] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)
    is_system: bool = False
    is_deleted: bool = False
    is_edited: bool = False


def parse_year(y: str) -> int:
    yi = int(y)
    if yi < 100:
        return 2000 + yi
    return yi


def normalize_sender(raw: str) -> str:
    raw = raw.strip().lstrip("~").strip()
    if raw in PHONE_ALIAS_MAP:
        return PHONE_ALIAS_MAP[raw]
    return raw


def parse_chat(txt_path: Path) -> list[Message]:
    """Parse a WhatsApp .txt export into Message objects."""
    out: list[Message] = []
    current: Optional[Message] = None
    with txt_path.open("r", encoding="utf-8", errors="ignore") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            m = DATE_LINE_RE.match(line)
            bracket_match = False
            if not m:
                m = DATE_LINE_RE_BRACKET.match(line)
                bracket_match = bool(m)
            if m:
                # finalize previous
                if current is not None:
                    out.append(current)
                year = parse_year(m.group("y"))
                month = int(m.group("m"))
                day = int(m.group("d"))
                hour = int(m.group("h"))
                if not bracket_match:
                    if m.group("ampm") == "PM" and hour != 12:
                        hour += 12
                    if m.group("ampm") == "AM" and hour == 12:
                        hour = 0
                minute = int(m.group("min"))
                try:
                    dt = datetime(year, month, day, hour, minute)
                except ValueError:
                    current = None
                    continue
                rest = m.group("rest")
                # Try sender:body
                sb = SENDER_BODY_RE.match(rest)
                if sb:
                    sender = sb.group("sender").strip()
                    body = sb.group("body")
                    is_edited = bool(EDITED_TAG_RE.search(body))
                    body = EDITED_TAG_RE.sub("", body)
                    is_deleted = body.strip() in {
                        "This message was deleted",
                        "You deleted this message",
                        "Null",
                    }
                    media: list[str] = []
                    if MEDIA_OMITTED in body:
                        media.append(MEDIA_OMITTED)
                        body = body.replace(MEDIA_OMITTED, "").strip()
                    am = ATTACH_RE.match(body) or ATTACH_BRACKET_RE.match(body)
                    if am:
                        media.append(am.group("file"))
                        body = ""
                    mentions = [mm for mm in MENTION_RE.findall(body)]
                    body = MENTION_RE.sub(lambda mm: mm.group(1), body)
                    current = Message(
                        dt=dt,
                        sender_raw=sender,
                        sender_norm=normalize_sender(sender),
                        body=body.strip(),
                        media_files=media,
                        mentions=mentions,
                        is_deleted=is_deleted,
                        is_edited=is_edited,
                    )
                else:
                    # System line (no sender:body)
                    current = Message(
                        dt=dt,
                        sender_raw="system",
                        sender_norm="system",
                        body=rest.lstrip("~ ").strip(),
                        is_system=True,
                    )
                continue
            # Continuation of previous message body
            if current is not None and not current.is_system:
                # check for "FILE.docx (file attached)" or "<attached: ...>" follow-up
                am = ATTACH_RE.match(line.strip()) or ATTACH_BRACKET_RE.match(line.strip())
                if am:
                    current.media_files.append(am.group("file"))
                else:
                    current.body = (current.body + "\n" + line).strip()
    if current is not None:
        out.append(current)
    return out


# ── Helpers ────────────────────────────────────────────────────────────────────

INVALID_PATH_CHARS = re.compile(r'[\\/:*?"<>|]')


def safe_filename(s: str, maxlen: int = 100) -> str:
    s = INVALID_PATH_CHARS.sub("_", s or "").strip()
    return s[:maxlen] or "untitled"


def slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s or "", flags=re.UNICODE).strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-") or "no-slug"


def yaml_escape(s) -> str:
    if s is None:
        return '""'
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def yaml_list(items: list) -> str:
    if not items:
        return "[]"
    return "\n  - " + "\n  - ".join(yaml_escape(i) for i in items)


def topic_links_for(group_name: str) -> list[str]:
    out = []
    for needle, links in GROUP_TOPIC_LINKS:
        if needle.lower() in group_name.lower():
            out.extend(links)
    seen = set()
    return [x for x in out if not (x in seen or seen.add(x))]


# ── Contact merging ───────────────────────────────────────────────────────────

# Lazily index Contacts/ once per run by name and email to avoid duplicates.
_contact_index: dict[str, Path] = {}


def index_contacts(gd_root: Path) -> None:
    global _contact_index
    _contact_index = {}
    cdir = gd_root / "Contacts"
    if not cdir.exists():
        return
    for f in cdir.glob("*.md"):
        _contact_index[f.stem.lower()] = f


def find_or_create_contact_path(gd_root: Path, name: str) -> Path:
    """Look up by stem case-insensitively; create at Contacts/<safe_name>.md if missing."""
    p = _contact_index.get(name.lower())
    if p:
        return p
    p = gd_root / "Contacts" / f"{safe_filename(name)}.md"
    _contact_index[name.lower()] = p
    return p


def update_contact_with_whatsapp(
    gd_root: Path, name: str, group_name: str, group_slug: str,
    msg_count: int, first_dt: datetime, last_dt: datetime,
) -> bool:
    """Append/update a 'WhatsApp messages' marker block in the Contact note."""
    if name.lower() in {ME.lower(), ME_DISPLAY_FOR_VAULT.lower(), "system"}:
        return False
    p = find_or_create_contact_path(gd_root, name)
    fresh = not p.exists()
    line = (
        f"- [[{group_slug}|{group_name}]] — {msg_count} messages "
        f"({first_dt.strftime('%Y-%m-%d')} → {last_dt.strftime('%Y-%m-%d')})"
    )
    block = f"{WA_BEGIN}\n## WhatsApp messages\n\n{line}\n{WA_END}"

    if fresh:
        contents = (
            "---\n"
            "type: contact\n"
            f"name: {yaml_escape(name)}\n"
            "email: \n"
            f"first_email: {first_dt.strftime('%Y-%m-%d')}\n"
            f"last_email: {last_dt.strftime('%Y-%m-%d')}\n"
            "email_count: 0\n"
            f"whatsapp_count: {msg_count}\n"
            "tags:\n  - contact\n  - whatsapp\n"
            "---\n\n"
            f"# {name}\n\n"
            f"_Met via WhatsApp group [[{group_slug}|{group_name}]]._\n\n"
            f"{block}\n"
        )
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(contents, encoding="utf-8")
        return True

    text = p.read_text(encoding="utf-8")
    if WA_BEGIN in text and WA_END in text:
        # Pull existing block, append/replace this group's line
        new_block_inner = []
        # Extract existing lines
        existing = re.search(
            re.escape(WA_BEGIN) + r"(.*?)" + re.escape(WA_END),
            text, re.DOTALL,
        ).group(1)
        existing_lines = []
        seen_group = False
        for ln in existing.splitlines():
            if ln.strip().startswith("- "):
                if f"[[{group_slug}|" in ln or f"[[{group_slug}]]" in ln:
                    existing_lines.append(line)  # replace
                    seen_group = True
                else:
                    existing_lines.append(ln)
        if not seen_group:
            existing_lines.append(line)
        new_block = WA_BEGIN + "\n## WhatsApp messages\n\n" + "\n".join(existing_lines) + "\n" + WA_END
        text = re.sub(
            re.escape(WA_BEGIN) + r".*?" + re.escape(WA_END),
            new_block, text, count=1, flags=re.DOTALL,
        )
    else:
        text = text.rstrip() + "\n\n" + block + "\n"

    # Bump whatsapp_count if present, else inject under email_count
    if "whatsapp_count:" in text:
        # for simplicity, just sum across blocks at next run; safe overwrite with msg_count
        pass
    else:
        text = re.sub(
            r"^(email_count:.*)$",
            r"\1\nwhatsapp_count: " + str(msg_count),
            text, count=1, flags=re.MULTILINE,
        )

    p.write_text(text, encoding="utf-8")
    return False


# ── Vault writers ─────────────────────────────────────────────────────────────


def write_source_stub(vault: Path, raw_dir: Path, file_name: str) -> None:
    src_path = raw_dir / file_name
    if not src_path.exists():
        return
    stub = vault / "_Sources" / f"{safe_filename(file_name)}.md"
    stub.parent.mkdir(parents=True, exist_ok=True)
    ext = src_path.suffix.lower().lstrip(".")
    kind_map = {
        "jpg": "image", "jpeg": "image", "png": "image", "webp": "image",
        "mp4": "video", "mov": "video",
        "opus": "audio", "mp3": "audio", "m4a": "audio", "wav": "audio",
        "pdf": "pdf", "docx": "doc", "odt": "doc", "txt": "text",
        "csv": "data", "ipynb": "notebook", "zip": "archive",
    }
    kind = kind_map.get(ext, "file")
    # Canonicalize before persisting — sandbox paths die when the session does.
    canon = canonicalize_to_host(src_path)
    raw_str = canon.replace('"', '\\"')
    uri_str = "file://" + canon.replace(" ", "%20")
    stub.write_text(
        "---\n"
        "type: source\n"
        f"raw_path: \"{raw_str}\"\n"
        f"raw_path_uri: \"{uri_str}\"\n"
        f"kind: {kind}\n"
        f"extension: {ext}\n"
        f"size_bytes: {src_path.stat().st_size}\n"
        "tags:\n  - source\n  - whatsapp\n"
        "---\n\n"
        f"# {file_name}\n\n"
        f"**Kind:** {kind}\n"
        f"**Open in macOS:** [Open]({uri_str})\n",
        encoding="utf-8",
    )


def render_message_md(m: Message) -> str:
    """Render one message as a markdown bullet."""
    if m.is_system:
        return f"- _{m.dt.strftime('%H:%M')} · system: {m.body}_"
    if m.is_deleted:
        return f"- _{m.dt.strftime('%H:%M')} · [[{m.sender_norm}]] · (deleted message)_"
    media_md = ""
    for f in m.media_files:
        if f == MEDIA_OMITTED:
            continue
        media_md += f" · [[{f}]]"
    edited_marker = " _(edited)_" if m.is_edited else ""
    body = m.body.strip()
    if "\n" in body:
        body = body.replace("\n", "\n    ")
    return f"- **{m.dt.strftime('%H:%M')}** [[{m.sender_norm}]]: {body}{edited_marker}{media_md}"


def write_month_bundle(
    vault: Path, group_name: str, group_slug: str, year_month: str,
    messages: list[Message], topic_links: list[str],
) -> Path:
    p = vault / "Messages" / group_slug / f"{year_month}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    front = [
        "---",
        "type: whatsapp-month",
        f"group: {yaml_escape(group_name)}",
        f"month: {year_month}",
        f"message_count: {len(messages)}",
        "tags:\n  - whatsapp\n  - chat-bundle",
        "---",
        "",
        f"# {group_name} — {year_month}",
        "",
        f"**Messages:** {len(messages)} · **Group:** [[{group_slug}|{group_name}]]",
        "",
    ]
    if topic_links:
        front.append("**Topics:** " + ", ".join(f"[[{t}]]" for t in topic_links))
        front.append("")
    # Group by day
    by_day: dict[str, list[Message]] = defaultdict(list)
    for m in messages:
        by_day[m.dt.strftime("%Y-%m-%d")].append(m)
    body_lines: list[str] = []
    for day in sorted(by_day):
        body_lines.append(f"## {day}")
        body_lines.append("")
        for m in by_day[day]:
            body_lines.append(render_message_md(m))
        body_lines.append("")
    p.write_text("\n".join(front + body_lines), encoding="utf-8")
    return p


def write_chat_moc(
    vault: Path, group_name: str, group_slug: str,
    messages: list[Message], months_written: list[str],
    topic_links: list[str], participant_counts: Counter,
) -> Path:
    p = vault / "Chats" / f"{group_slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    if not messages:
        first = last = "?"
    else:
        first = messages[0].dt.strftime("%Y-%m-%d")
        last = messages[-1].dt.strftime("%Y-%m-%d")
    parts = [
        "---",
        "type: chat",
        f"group: {yaml_escape(group_name)}",
        "platform: whatsapp",
        f"first_message: {first}",
        f"last_message: {last}",
        f"message_count: {len(messages)}",
        "tags:\n  - chat\n  - whatsapp",
        "---",
        "",
        f"# {group_name}",
        "",
        f"**Platform:** WhatsApp · **Messages:** {len(messages)} · **Period:** {first} → {last}",
        "",
    ]
    if topic_links:
        parts.append("**Topics:** " + ", ".join(f"[[{t}]]" for t in topic_links))
        parts.append("")
    parts.append("## Participants")
    parts.append("")
    for name, count in participant_counts.most_common():
        if name == "system":
            continue
        wikilink = f"[[{ME_DISPLAY_FOR_VAULT}|me]]" if name == ME else f"[[{name}]]"
        parts.append(f"- {wikilink} — {count} messages")
    parts.append("")
    parts.append("## Months")
    parts.append("")
    for ym in months_written:
        parts.append(f"- [[{ym}|{ym}]]")
    p.write_text("\n".join(parts), encoding="utf-8")
    return p


def write_vault_index(
    vault: Path, group_summaries: list[dict], total_msgs: int, total_media: int,
) -> None:
    p = vault / "_Index.md"
    parts = [
        "---",
        "type: index",
        "tags:\n  - moc\n  - whatsapp",
        "---",
        "",
        "# Whatsapp — Map of Content",
        "",
        f"**Groups:** {len(group_summaries)} · **Messages:** {total_msgs:,} · **Media:** {total_media}",
        "",
        "## Groups",
        "",
    ]
    for g in group_summaries:
        parts.append(
            f"- [[{g['slug']}|{g['name']}]] — {g['count']} messages "
            f"({g['first']} → {g['last']})"
        )
    parts.append("")
    parts.append("## Conventions")
    parts.append("")
    parts.append("- **Chats/`<slug>.md`** — group MOC: participants, monthly index, topics")
    parts.append("- **Messages/`<slug>/<YYYY-MM>.md`** — month-bundled transcripts")
    parts.append("- **`_Sources/<file>.md`** — stub per media file (image, video, doc, audio)")
    parts.append("- Contact back-links to `Vaults/Google Data/Contacts/` (shared people directory)")
    p.write_text("\n".join(parts), encoding="utf-8")


# ── Main ──────────────────────────────────────────────────────────────────────


def process_chat_folder(
    chat_dir: Path, vault: Path, gd_root: Path, dry_run: bool = False,
) -> dict:
    txt_files = list(chat_dir.glob("WhatsApp Chat with *.txt"))
    if not txt_files:
        print(f"  skip {chat_dir.name}: no chat .txt found", file=sys.stderr)
        return {}
    txt = txt_files[0]
    group_name = txt.stem.replace("WhatsApp Chat with ", "")
    group_slug = slugify(group_name)
    print(f"  Parsing: {group_name}")
    messages = parse_chat(txt)
    print(f"    {len(messages)} messages")

    # Aggregate data
    media_files: set[str] = set()
    by_month: dict[str, list[Message]] = defaultdict(list)
    participant_counts: Counter = Counter()
    contact_first: dict[str, datetime] = {}
    contact_last: dict[str, datetime] = {}
    contact_count: Counter = Counter()
    for m in messages:
        if not m.is_system:
            participant_counts[m.sender_norm] += 1
            if m.sender_norm not in (ME, "system"):
                contact_first.setdefault(m.sender_norm, m.dt)
                contact_first[m.sender_norm] = min(contact_first[m.sender_norm], m.dt)
                contact_last[m.sender_norm] = max(contact_last.get(m.sender_norm, m.dt), m.dt)
                contact_count[m.sender_norm] += 1
        for f in m.media_files:
            if f != MEDIA_OMITTED:
                media_files.add(f)
        by_month[m.dt.strftime("%Y-%m")].append(m)

    topic_links = topic_links_for(group_name)

    if dry_run:
        return {
            "name": group_name, "slug": group_slug, "count": len(messages),
            "first": messages[0].dt.strftime("%Y-%m-%d") if messages else "?",
            "last": messages[-1].dt.strftime("%Y-%m-%d") if messages else "?",
            "participants": dict(participant_counts),
            "months": list(sorted(by_month)),
            "media": len(media_files),
        }

    # Write source stubs for media
    for f in sorted(media_files):
        write_source_stub(vault, chat_dir, f)

    # Write month bundles
    months_written = []
    for ym in sorted(by_month):
        write_month_bundle(vault, group_name, group_slug, ym, by_month[ym], topic_links)
        months_written.append(ym)

    # Write chat MOC
    write_chat_moc(vault, group_name, group_slug, messages, months_written, topic_links, participant_counts)

    # Update Contacts
    for name, count in contact_count.items():
        update_contact_with_whatsapp(
            gd_root, name, group_name, group_slug,
            count, contact_first[name], contact_last[name],
        )

    return {
        "name": group_name, "slug": group_slug, "count": len(messages),
        "first": messages[0].dt.strftime("%Y-%m-%d") if messages else "?",
        "last": messages[-1].dt.strftime("%Y-%m-%d") if messages else "?",
        "participants": dict(participant_counts),
        "months": months_written,
        "media": len(media_files),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=str(DEFAULT_RAW))
    ap.add_argument("--vaults", default=str(DEFAULT_VAULTS))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    raw = Path(args.raw)
    vaults = Path(args.vaults)
    if not raw.exists():
        print(f"ERROR: RAW folder not found: {raw}", file=sys.stderr)
        return 2
    if not vaults.exists():
        print(f"ERROR: vaults folder not found: {vaults}", file=sys.stderr)
        return 2

    vault = vaults / "Whatsapp"
    gd_root = vaults / "Google Data"
    if not args.dry_run:
        vault.mkdir(parents=True, exist_ok=True)

    index_contacts(gd_root)

    summaries = []
    chat_dirs = sorted([p for p in raw.iterdir() if p.is_dir() and p.name.startswith("WhatsApp Chat with")])
    print(f"Found {len(chat_dirs)} chat folders.")
    for cd in chat_dirs:
        s = process_chat_folder(cd, vault, gd_root, dry_run=args.dry_run)
        if s:
            summaries.append(s)

    if args.dry_run:
        print("\nDRY RUN summary:")
    total_msgs = sum(s["count"] for s in summaries)
    total_media = sum(s["media"] for s in summaries)
    print(f"\nTotal: {total_msgs:,} messages across {len(summaries)} chats; {total_media} media files.")
    for s in summaries:
        print(f"  - {s['name']} — {s['count']} msgs · {len(s['months'])} months · "
              f"{len(s['participants'])} participants · {s['media']} media")

    if not args.dry_run:
        write_vault_index(vault, summaries, total_msgs, total_media)
        # Build per-contact redirect stubs under Whatsapp/Contacts/ so wikilinks
        # like [[Barbara Bernardo]] resolve INSIDE the WhatsApp vault instead of
        # producing 6,000+ broken links on each sync. The stubs are thin
        # redirects to the canonical Google Data/Contacts/<Name>.md. Defers to
        # build_whatsapp_contact_stubs.py if present; otherwise no-op so this
        # remains stdlib-safe.
        stub_script = Path(__file__).parent / "build_whatsapp_contact_stubs.py"
        if stub_script.exists():
            import subprocess
            print("\nBuilding Whatsapp/Contacts/ redirect stubs ...")
            subprocess.run(
                [sys.executable, str(stub_script),
                 "--vaults", str(vaults), "--apply"],
                check=False,
            )
        print(f"\nWrote vault to: {vault}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
