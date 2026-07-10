---
name: second-brain-daily
description: >-
  Write or append to today's daily note in your Second Brain Daily vault, capturing
  what got done, decisions made, files touched, entities mentioned, and ideas worth
  keeping. Trigger when the user says "log today", "save this session", "wrap up the day",
  "end of day note", "journal today", "add to my daily notes", "update the daily log",
  or similar phrases that signal he wants the current conversation captured for the
  vault. Also trigger when invoked by the Daily Note Builder scheduled task at 10pm,
  which passes the date and a list of session transcripts to summarize. Always idempotent
  — appends rather than overwrites if today's note already exists. Always defers to the
  second-brain-researcher skill when entity wikilinks need verifying. Never writes
  outside Vaults/Daily/.
---

# Second Brain Daily Note Builder

Captures the day's work into `Vaults/Daily/<YYYY>/<MM>/<YYYY-MM-DD>.md`, structured for both readability and graph integration.

## When to use this skill

**Manual triggers** (the user is in an active conversation):
- "log today"
- "save this session" / "save today"
- "wrap up the day" / "end of day"
- "journal today"
- "add this to my daily notes"
- "update the daily log"

**Automated trigger:**
- The `Daily Note Builder` scheduled task fires at 10:00 PM local time, calls this skill with `mode=scheduled` and a list of today's Cowork session IDs to read.

## Output location

`/path/to/your/second-brain/Vaults/Daily/<YYYY>/<MM>/<YYYY-MM-DD>.md`

The Daily vault folders for the current year/month are auto-created if missing. Year and month directories use 4-digit and 2-digit zero-padded numbers (`2026/05/2026-05-09.md`).

## Note structure

Every daily note follows this template. Sections are stable so that future scripts can reliably parse and append.

```markdown
---
type: daily
date: YYYY-MM-DD
tags:
  - daily
sessions: <count of Cowork sessions captured today>
last_updated: YYYY-MM-DDTHH:MM:SS+00:00
---

# YYYY-MM-DD — <day-of-week>

## Summary

<1–3 paragraphs synthesizing what got done today across all captured sessions.
This is the narrative spine; write it like you're telling the user what he did,
not in third person.>

## Files & vaults touched

- [Vault note] [[Note Stem]] — <one-line description of the change>
- [RAW file] [name](computer:///path/to/file) — <what changed>
- ...

## Entities mentioned

People, projects, topics, threads referenced today — listed as wikilinks so
this note appears in their backlink panels.

- People: [[Contact A]], [[Contact B]]
- Projects: [[Project X]], [[Project Y]]
- Topics: [[Topic Z]]
- Threads: [[Thread title]]

## Ideas & open questions

- <half-formed idea worth keeping>
- <decision deferred or open question>

## Sessions

<!-- session-block:start -->
### Session <id> — <start time> → <end time>
- Model: <model id>
- Tool calls: <count>
- Notable: <one-liner of what this session was about>

<!-- session-block:end -->
```

## Idempotency

The note must be safe to write multiple times per day:

1. If today's file does **not** exist → write the full template above.
2. If today's file **does** exist → load it, append a new "## Update at HH:MM" section under each top-level heading (Summary, Files, Entities, Ideas, Sessions). Never duplicate session entries — check each session's ID before adding.
3. Update the `last_updated` field in frontmatter on every write.
4. The `<!-- session-block:start --> ... <!-- session-block:end -->` markers wrap the Sessions section so the helper script can append cleanly.

The bundled `scripts/daily_note_builder.py` handles all of this — call it directly rather than reimplementing.

## Workflow

### Manual mode (the user is in chat right now)

1. Determine today's date (use the current date from environment, not your knowledge cutoff).
2. Synthesize the four content sections from the **current conversation**:
   - **Summary**: what we discussed, decisions reached, problems solved. Write in your voice — "you decided to…", "we built…", "you asked me to…". 1–3 paragraphs.
   - **Files & vaults touched**: scan back through the conversation for any file paths you wrote/edited/created or vault notes you cited. Use `[[wikilink]]` for vault notes and `computer://` URLs for raw files. **Do not invent files.**
   - **Entities mentioned**: every person/project/topic with an existing vault note that came up. Verify wikilink targets exist by spot-checking with the second-brain-researcher skill if uncertain.
   - **Ideas & open questions**: things the user flagged as "want to do later", "not sure about", "need to think about", or that were genuinely unresolved when the conversation ended.
3. Build a session entry with whatever metadata you can derive (start time of conversation if known; otherwise just label it `Session — manual log`).
4. Run `python3 <skill-path>/scripts/daily_note_builder.py write --date YYYY-MM-DD --content-file <path-to-staged-content.md>` to write or append.
5. Confirm to the user with a `computer://` link to today's daily note.

### Scheduled mode (10pm task fires)

1. The task prompt will include today's date.
2. Use the `mcp__session_info__list_sessions` MCP to find today's Cowork sessions.
3. For each session, use `mcp__session_info__read_transcript` to read the transcript.
4. Process each session through the same synthesis as manual mode — but now you have the raw transcript so be more thorough.
5. Build the daily note with one session entry per Cowork session, deduped by session ID.
6. Write the file.
7. Print a one-line confirmation (the scheduled task captures stdout).

## Citation rules

- Vault notes → `[[Note Stem]]`
- RAW files → `[filename](computer:///path/to/your/second-brain with URL-encoded spaces
- People → check `Vaults/Google Data/Contacts/<Name>.md` exists; if so, link as `[[<Name>]]`. If not, mention them by name without a wikilink (don't fabricate notes).
- Projects → check the relevant vault's `_Index.md` or top-level folders before linking.

## What to avoid

- **Don't write anywhere outside `Vaults/Daily/`.** This skill has one job and one location.
- **Don't dump the entire conversation transcript into the note.** Synthesize. The point is to capture the *insights* and *artifacts*, not transcribe.
- **Don't fabricate wikilinks.** If you're not sure a contact note exists, drop the wikilink and just write the name. Empty wikilinks pollute the graph.
- **Don't overwrite a non-empty existing file.** Always append.
- **Don't include sensitive secrets** (API keys, passwords, OAuth tokens) even if they came up in the chat. Strip them.
- **Don't moralize about how the user spent his day.** Just report what happened.

## Reference

- `references/daily-note-template.md` — the full template with example content filled in.

## Helper script

`scripts/daily_note_builder.py` is the workhorse:

```bash
# Write/append today's note from a staged content markdown file
python3 daily_note_builder.py write \
    --date 2026-05-09 \
    --content-file /tmp/today_content.md

# Just check what file would be touched (no write)
python3 daily_note_builder.py path --date 2026-05-09

# List all sessions visible from the session_info MCP (debug)
python3 daily_note_builder.py list-sessions
```

The script handles directory creation, frontmatter management, session-block dedup, and the "first write vs append" branching.
