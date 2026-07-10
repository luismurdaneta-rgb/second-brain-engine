---
name: second-brain-claude-archiver
description: >-
  Archive a Claude conversation as a full transcript note in Vaults/Claude/. Trigger
  when the user says "archive this conversation", "save this chat", "save this whole
  conversation", "log this convo", "back up this session", "add this conversation to
  my second brain", or similar phrases that signal he wants the full back-and-forth
  preserved (not just a summary). Also trigger when invoked by the daily 10pm scheduled
  task, which calls this skill once per Cowork session of the day. Different from
  second-brain-daily — that one writes a synthesis; this one archives the evidence.
  Always idempotent — sessions are deduped by session_id, never overwritten if already
  archived. Always writes inside Vaults/Claude/ only — never into other vaults.
---

# Second Brain Claude Conversation Archiver

Saves a Claude/Cowork conversation as a full transcript note in `Vaults/Claude/0_Inbox/`, mirroring the shape of the existing `Vaults/ChatGPT/` so cross-search and the graph view work identically.

## When to use this skill

**Manual triggers:**
- "archive this conversation" / "save this chat" / "save this whole conversation"
- "log this convo" / "back up this session"
- "add this conversation to my second brain"
- "remember this chat for later"

**Automated trigger:**
- The 10pm `second-brain-daily-note` scheduled task calls this skill once per Cowork session it found that day, after it writes the daily synthesis. The two skills complement each other: daily = synthesis, archiver = evidence.

## Difference from second-brain-daily

| | second-brain-daily | second-brain-claude-archiver |
|---|---|---|
| Output | One note per **day** | One note per **conversation** |
| Location | `Vaults/Daily/<Y>/<M>/<date>.md` | `Vaults/Claude/0_Inbox/<title>.md` |
| Content | Synthesized summary | Full user/assistant transcript |
| Idempotent on | Date | session_id |
| Volume | ~1 note/day | ~1–N notes/day depending on activity |

When the daily task fires, it writes the daily note AND calls this skill for each session — so you get both layers.

## Output location

`/path/to/your/second-brain/Vaults/Claude/0_Inbox/<safe-title>.md`

The title is derived from the conversation's first user message (truncated to ~80 chars). If a same-titled note already exists from a different session, append a 6-char session-id suffix to disambiguate. Newly archived conversations always land in `0_Inbox/`; you triage them into PARA folders manually over time, just like with the ChatGPT vault.

## Note structure

```markdown
---
type: claude-conversation
source: cowork
session_id: 50767180-8bde-4b83-8d61-53728e73873c
title: "<first user prompt, truncated>"
created: 2026-05-09T06:14:00
last_message: 2026-05-09T22:14:00
model: claude-opus-4-7
message_count: 38
tool_call_count: 152
para: inbox
topic: ""
tags:
  - claude-conversation
  - cowork
---

# <conversation title>

**👤 User — 06:14**
<original user prompt, full text>

**🤖 Assistant — 06:14**
<assistant reply>

**👤 User — 06:18**
<follow-up>

...

<!-- connections:start -->
## Connections

- **Topic:** [[<topic>]]
- **Daily note:** [[<YYYY-MM-DD>]]
- **Sessions captured:** [`Cowork session log`](computer:///path/to/raw/transcript)
<!-- connections:end -->
```

## Idempotency

Critical because the scheduled task may run multiple times in edge cases:

1. Before writing, check whether any existing note in `Vaults/Claude/` (across all PARA folders) already has matching `session_id` frontmatter. The bundled script does this lookup.
2. If found → skip silently with a "already archived" message. **Never overwrite.** The user may have moved the note out of `0_Inbox/` and edited it.
3. If not found → write to `0_Inbox/`.

## Workflow

### Manual mode (the user is in chat)

1. The current Cowork session_id is the one you're running in. Get today's date.
2. Reconstruct the conversation transcript from the messages you can see in your context — start from the first user prompt and walk through every exchange.
3. For tool calls, summarize as one line ("→ called `Edit` on file X" / "→ ran bash command Y") rather than pasting full tool output. Tool output is noise in a conversation archive.
4. Synthesize the title from the first substantive user prompt (skip greetings).
5. Run the bundled `scripts/archive_conversation.py write` (see below) to write the note.
6. Confirm with a `computer://` link.

### Scheduled mode (10pm task)

1. The task gives you a list of session IDs for the day.
2. For each session ID:
   - Use `mcp__session_info__read_transcript` to get the full transcript JSON.
   - Convert it to the markdown shape above. The transcript JSON has structured messages with timestamps, model name, tool calls — render them.
   - Call the helper script with the session metadata.
3. Print a summary: `archived 3 sessions → Vaults/Claude/0_Inbox/{a,b,c}.md`.

## Citation rules

- **Daily note backlink** is mandatory — every transcript note links to `[[<YYYY-MM-DD>]]` in its connections block.
- **Topic** field is left empty by default. The user fills it manually as they triage from `0_Inbox/`. Don't guess.
- Transcript content shouldn't itself contain wikilinks unless they were spoken in the original conversation — the goal is fidelity to what was actually said.

## Tool-call rendering

Tool calls bloat transcripts and are usually noise for the reader. Default rendering:

```markdown
**🤖 Assistant — 06:18**

> _called `Edit` on `/Users/.../foo.py`_
> _ran bash: `python3 sb_search.py "Sarah"`_

Here's what I found...
```

For the few tools whose output IS the message (e.g., `mcp__cowork__present_files`, web fetches), keep the result inline.

## What to avoid

- **Don't write outside `Vaults/Claude/`.** Period.
- **Don't deduplicate by title — only by session_id.** Two different conversations can legitimately have the same title.
- **Don't include sensitive secrets in the transcript** (API keys, passwords, OAuth tokens) — strip them. Common patterns: lines starting with `Authorization:`, env-var assignments to keys named `*_KEY`, `*_TOKEN`, `*_SECRET`.
- **Don't truncate the conversation** unless it exceeds 200,000 characters; in that case truncate at the end with `_…(truncated, full transcript at <raw_path>)_` and save the full transcript as a `_Sources/` stub.
- **Don't try to extract topics or concepts.** Just archive. The graphify-style processing is a separate step (and not yet built for the Claude vault).

## Helper script

`scripts/archive_conversation.py`:

```bash
# Write a session as a transcript note
python3 archive_conversation.py write \
    --session-id <uuid> \
    --created 2026-05-09T06:14:00 \
    --last-message 2026-05-09T22:14:00 \
    --model claude-opus-4-7 \
    --message-count 38 \
    --tool-call-count 152 \
    --transcript-file /tmp/transcript.md \
    --title "Building the Second Brain skills"

# Check whether a session is already archived
python3 archive_conversation.py exists --session-id <uuid>

# List all archived sessions (debug)
python3 archive_conversation.py list
```

The `--transcript-file` is a markdown file with the rendered conversation (no frontmatter — the script adds that). The script picks the right destination filename, dedupes by session_id, and stitches in the connections block.

## Reference files

- `references/transcript-rendering.md` — exact format for converting session_info MCP output to the conversation markdown body.
