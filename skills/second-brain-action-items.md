---
name: second-brain-action-items
description: Sweep every vault in your Second Brain for action items, TODOs, and open commitments buried in notes, reviews, and briefs — then produce three outputs in one shot: append new items to TASKS.md, write a dated rollup at Vaults/Daily/Action-items/<YYYY-MM-DD>-action-items.md, and render an interactive Cowork dashboard. Distinct from second-brain-blind-spots (which surfaces what's MISSING) and from second-brain-situation-explainer (broader attention dashboard). This skill answers one narrow question: "what do I actually owe right now?" Trigger phrases — "extract action items", "what do I need to do", "pull my TODOs", "what's on my plate from the vault", "find my action items", "scan my notes for what's pending", "what did I commit to", "what am I owing", "what's outstanding". Also fires when invoked by the scheduled task `action-items-daily` at 7am. Do NOT trigger for retrieval (researcher), noise removal (vault-cleaner), or dropped-goals recon (blind-spots).
version: 0.1.0
---

# Second Brain — Action Items

Scan every vault for things the user still owes the world. Three outputs every run, all idempotent.

## When to trigger

Yes:
- "Extract action items"
- "What do I need to do"
- "Pull my TODOs"
- "What's on my plate from the vault"
- "Find my action items"
- "What did I commit to"
- "What's outstanding"
- "Scan my notes for pending stuff"
- Scheduled morning run (`action-items-daily` at 7am local)

No:
- "What am I missing" / "blind spots" → `second-brain-blind-spots`
- "Where should I focus" / "show me my situation" → `second-brain-situation-explainer`
- "Who is X" / "find me the doc about Y" → `second-brain-researcher`
- "Clean up the vault" → `second-brain-vault-cleaner`

The line is: this skill returns a list of *concrete things to do*. If the user wants pushback, prioritization, or recon, route elsewhere.

## What counts as an action item

The extractor recognises six categories. Confidence weights in parentheses.

1. **`checkbox`** — unchecked `- [ ]` items. (0.95) The cleanest signal.
2. **`marker`** — lines beginning with `TODO:`, `FIXME:`, `ACTION:`, `NEXT:`, `FOLLOW-UP:`, `OWED:`, `REPLY:`. (0.9)
3. **`brief_ask`** — lines beginning with `Action:`, `Next step:`, `Follow up:`, `To-do:`, `Owed:`, etc. Common in meeting briefs and recaps. (0.85)
4. **`self_commit`** — imperative self-talk: "I need to X", "I should X", "Need to X", "Remember to X". (0.65 — lower confidence, more false positives.)
5. **`promised_reply`** — inside Google Data threads where the user is the sender, phrases like "I'll get back to you", "let me check", "I'll send", "I'll review". (0.7)
6. **`waiting_on`** — `WAITING:` / `BLOCKED:` markers. (0.85) Tracked separately as items waiting on *others*.

Quoted lines (starting with `>`) are skipped — those are email reply quotes, not fresh commitments.

## Workflow

1. **Run the extractor.**
   ```bash
   python3 _scripts/extract_action_items.py \
     --vault-root "/path/to/your/second-brain/Vaults" \
     --days 30 \
     --out /tmp/action_items.json
   ```
   - Default scope is the last 30 days (recent enough to be live, old enough to catch carry-over).
   - Drop `--days` to sweep everything.
   - Output JSON has `items` grouped by vault, each item with `id`, `path`, `line`, `category`, `text`, `score`, `dates_in_text`, and source-file metadata.

2. **Read the JSON.** Each item is already deduped within and across vaults. Skim by descending `score`.

3. **Triage in your head.** For each item ask:
   - Is this still actionable? (A "follow up with X" from 2024 about a closed project is dead — drop it.)
   - Does it have a date already? (If yes, surface that prominently.)
   - Is it actually *your* job? (For `promised_reply` and `waiting_on`, double-check the direction.)

4. **Produce the three outputs.** Templates below.

5. **Update memory.** Save (as `project` memory) the date, count, and any false-positive patterns worth tuning out next run.

## Output 1 — append to TASKS.md

Path: `/path/to/your/second-brain/Vaults/_TASKS.md`. Create if missing. Append-only — never delete a user-edited line.

Use a marker block per day so re-runs replace just today's additions instead of duplicating. Format:

```markdown
<!-- action-items:start <YYYY-MM-DD> -->
## From the vault — <YYYY-MM-DD>

- [ ] {action text} — _<vault>/<short path>_ ^<id>
- [ ] ...
<!-- action-items:end <YYYY-MM-DD> -->
```

Rules:
- Wrap each batch in dated marker comments. On re-run for the same day, replace the block.
- The `^<id>` is the item's 12-char fingerprint from the JSON — gives Obsidian a block-ref and gives the next run a dedupe key.
- Only include items with `score >= 0.75`. Lower-confidence stuff goes in the dated note (Output 2) but not in TASKS.md.
- Sort by score within the block.
- If a fingerprinted ID already appears anywhere else in TASKS.md, skip it (it's already known).

## Output 2 — dated markdown note

Path: `Vaults/Daily/Action-items/<YYYY-MM-DD>-action-items.md`. Idempotent — if the file exists, append a new `## Update at HH:MM` section rather than overwriting prior content.

Template:

```markdown
---
type: action-items
date: <YYYY-MM-DD>
voice: assistant
scope: <e.g. "last 30 days, all vaults">
---

# Action items — <YYYY-MM-DD>

> Scanned <N> files across <M> vaults. Surfaced <K> open items (after dedupe).

## Highest confidence (checkboxes + markers + brief asks)
- **{text}** — `<path>:<line>` (score <S>) {dates if any}
- ...

## Self-stated commitments (lower confidence — verify)
- ...

## Promised replies (Google Data — outbox)
- ...

## Waiting on others
- ...

## By vault
- **Daily** — <n>
- **Google Data** — <n>
- **ChatGPT** — <n>
- **Claude** — <n>
- **Whatsapp** — <n>
- **DreamWorks** — <n>
- **Zigurat** — <n>
- **Personal** — <n>

---
_Run: <ISO timestamp>, files scanned: <n>, items after dedupe: <n>_
```

Always use real wikilinks for file references when the path resolves inside the vault: `[[<vault>/<path>|<filename>]]`. That wires the note into the Obsidian graph.

## Output 3 — Cowork artifact

Call `mcp__cowork__create_artifact` with a single-page HTML view. Required affordances:

- **Filter row:** category checkboxes (checkbox / marker / brief_ask / self_commit / promised_reply / waiting_on), vault dropdown, min-score slider, free-text search.
- **Sort:** by score (default), by vault, by file mtime.
- **Each item row:** title (the text), pill for category + vault, file path rendered as a `computer://` link, dates surfaced if any, and three actions:
  - "Mark done" — calls `sendPrompt("Mark action item <id> done")`
  - "Snooze 7d" — calls `sendPrompt("Snooze action item <id> for a week")`
  - "Drop (false positive)" — calls `sendPrompt("Drop action item <id> as not actionable — tune the extractor")`
- **State persistence:** keep dismissed/snoozed IDs in `localStorage` so reopening the artifact keeps state.
- **Header counts** that update as filters change.

Load Grid.js from CDN if helpful — otherwise plain HTML is fine. Keep it visually plain; this is a working list, not a showcase.

## Scheduled run — `action-items-daily`

The Cowork scheduled task runs every day at 7:00 AM local time. It:

1. Runs the extractor with `--days 30`.
2. Generates the dated markdown note (Output 2).
3. Updates `_TASKS.md` (Output 1).
4. Skips the artifact (no live Cowork session).

On-demand invocations always produce all three outputs.

## Invocation cheat sheet

```bash
# Default daily run — last 30 days
python3 _scripts/extract_action_items.py \
  --vault-root "/path/to/your/second-brain/Vaults" \
  --out /tmp/action_items.json

# Full sweep — every file ever
python3 _scripts/extract_action_items.py \
  --vault-root "/path/to/your/second-brain/Vaults" \
  --days 9999 --out /tmp/action_items_all.json

# Audit completed items too
python3 _scripts/extract_action_items.py \
  --vault-root "/path/to/your/second-brain/Vaults" \
  --include-checked --out /tmp/action_items_audit.json
```

## Memory protocol

After each run, save (as `project` memory):
- Run date and item count.
- Any item IDs the user explicitly dropped as false positives — next run, filter them out *before* the chat output.
- Any extractor-tuning notes (regex tweaks made, vaults added/removed).

On subsequent runs, READ this memory first to:
- Avoid re-surfacing dropped IDs.
- Avoid re-creating completed items (the memory entry for "X marked done on YYYY-MM-DD" wins over the JSON if the same fingerprint appears).

## Style for the chat reply

When invoked interactively (not by the scheduled task), the chat reply should be the **top 10 by score**, grouped lightly:

```
**Top action items from the vault — <date>**

Checkboxes & markers (highest confidence)
1. {text} — `<vault>/<path>` ^<id>
2. ...

You wrote that you'd do this (verify)
6. {text} — `<vault>/<path>` ^<id>
...

Promised replies (outbox)
9. ...

Waiting on others
10. ...

[View live dashboard](computer:///path/to/artifact) · [Open TASKS.md](computer:///path/to/your/second-brain/Vaults/_TASKS.md) · [Open today's rollup](computer://...)
```

No trailing postamble. No "let me know if…". Last line is the link row.

## What this skill does NOT do

- Does NOT prioritize across all your goals — `second-brain-situation-explainer` is the right tool for that.
- Does NOT push back on whether the items are worth doing — `thought-partner` and `blind-spots` do that.
- Does NOT modify any vault content besides `_TASKS.md` and `Vaults/Daily/Action-items/<date>.md`. Everything else is read-only.
- Does NOT delete checked items — only surfaces them in `--include-checked` audit mode.
- Does NOT call an LLM during extraction — it's pure regex + dedupe. Synthesis (Output 2 prose, ranking) is Stage 2 (you).

## Files in this skill

- `SKILL.md` — this file.
- The extractor lives in the vault, not in the skill folder: `/path/to/your/second-brain/_scripts/extract_action_items.py`. (Convention match: blind-spots, vault-cleaner, and curator all keep scripts under `_scripts/`.)

## Troubleshooting

- **Lots of `self_commit` false positives** — the `I should / Need to / Remember to` regex is intentionally loose. If a particular pattern keeps misfiring, save the dropped IDs to memory and consider tightening the regex in `_scripts/extract_action_items.py`.
- **Same item appears every day** — check whether it's been added to TASKS.md but not checked off. The fingerprint dedupe only fires if the item already exists in TASKS.md as `^<id>`.
- **Nothing comes back** — re-run with `--days 9999`. Some vaults (DreamWorks, Zigurat) move slowly and the 30-day window will skip them.
- **Artifact is empty** — confirm the JSON path passed to the artifact builder is reachable. The Cowork artifact reads the JSON over a tool call, not from the filesystem directly.
