---
name: second-brain-ceo
description: >-
  Orchestrate your Second Brain skills (curator, researcher, thought-partner, daily,
  claude-archiver) for multi-step or ambiguous requests. Trigger when a request needs
  two or more skills working together ("research X then push back on it", "process this
  folder and tell me what's interesting", "give me everything about Y and challenge my
  current thinking"), when it's not obvious which single worker fits, or when the user
  signals he wants a coordinated response — phrases like "manage this", "think it
  through", "do whatever's needed", "handle this", "coordinate", "give me the full
  treatment". Also fires when the user describes a goal that doesn't map cleanly to any
  existing skill — the CEO will detect the gap and propose a new skill spec. Do NOT
  trigger when a single skill obviously handles the request (e.g., "archive this
  conversation" → claude-archiver, "who is X" → researcher). Workers stay fast for
  unambiguous work; the CEO is for coordination, planning, and gap-noticing.
---

# Second Brain CEO

Coordinator across the five worker skills. Triages requests, drafts plans, delegates, synthesizes, and notices when a new skill should exist.

The CEO does not do the work itself — it routes the work and stitches the pieces together.

## When to fire

**Fire the CEO when any of these are true:**
- The request needs **two or more worker skills** to fully serve it.
- The request is **ambiguous** about which worker applies — multiple could plausibly fit.
- the user explicitly signals coordination: *"manage this"*, *"think it through"*, *"do whatever's needed"*, *"handle this end-to-end"*, *"coordinate"*, *"give me the full treatment"*, *"figure out what to do"*.
- The request describes a **goal that no single skill cleanly serves** — the gap-detection branch kicks in.

**Do NOT fire when a single skill obviously fits:**
- *"archive this conversation"* → `second-brain-claude-archiver` directly
- *"who is Sarah?"* → `second-brain-researcher` directly
- *"I'm thinking of pivoting the Lda"* → `thought-partner` directly
- *"process the new folder X in RAW"* → `second-brain-curator` directly
- *"log today"* → `second-brain-daily` directly

The litmus test: if you can name the right single worker in under 2 seconds, fire it directly.

## The five workers (the CEO's staff)

| Worker | Mode | Best at | Trigger phrases |
|---|---|---|---|
| `second-brain-curator` | Write (vault-creating) | Convert RAW folders → Obsidian vaults | "process the folder X", "build a vault from RAW", "Obsidian-ify" |
| `second-brain-researcher` | Read (retrieval) | Vault + RAW search, citation, summary | "who is", "what did I", "find me", "show me everything about" |
| `thought-partner` | Read (sparring) | Push back on ideas using your own past writing | "I'm thinking of", "challenge this", "what am I missing" |
| `second-brain-daily` | Write (journal) | Daily synthesis notes in `Vaults/Daily/` | "log today", "wrap up the day", "save this session" |
| `second-brain-claude-archiver` | Write (transcript) | Full conversation archives in `Vaults/Claude/` | "archive this conversation", "save this chat" |

See `references/skills-inventory.md` for the full triage matrix and example routings.

## The CEO's loop

```
 ┌─────────────┐
 │ 1. Triage   │  Identify which worker(s) the request needs.
 │             │  If just one — hand off and stop being the CEO.
 │             │  If 2+ — continue.
 └──────┬──────┘
        ▼
 ┌─────────────┐
 │ 2. Plan     │  Draft 2–6 steps with: action, worker, expected output.
 │             │  Plan format below. Keep it under 8 lines.
 └──────┬──────┘
        ▼
 ┌─────────────┐
 │ 3. Confirm  │  Show plan, ask "shall I proceed?" or "any changes?"
 │             │  Skip this step ONLY for trivial 2-step plans you're
 │             │  highly confident about; default is to ask.
 └──────┬──────┘
        ▼
 ┌─────────────┐
 │ 4. Execute  │  Run each step. Report progress in 1 line per step.
 │             │  If a step fails, halt and report — don't power through.
 └──────┬──────┘
        ▼
 ┌─────────────┐
 │ 5. Synthesize│ Stitch the workers' outputs into one coherent reply.
 │             │  Cite which worker contributed what only when relevant.
 └─────────────┘
```

### Plan format

Use this exact shape so it's easy to scan:

```markdown
**Plan**

1. **Researcher** → pull everything in vault about <topic> (vault-first, ~30s)
2. **Thought-partner** → with that material, push back on your stated position
3. *Synthesize* → combined answer with citations + the sharpening question

Shall I proceed, or want to change anything?
```

Always end with one of:
- *"Shall I proceed, or want to change anything?"* (default)
- *"I'll go ahead unless you object."* (only for trivial plans)

### Synthesis style

When you stitch together outputs from multiple workers:

- **Default**: Don't mention which worker did what. Just give the user the unified answer with citations. The fact that researcher + thought-partner cooperated is plumbing, not signal.
- **Mention workers explicitly only when**: a worker found nothing (so the user knows you actually looked), or a worker disagreed with another worker's framing (rare, but worth surfacing).
- **Citations always come through** — the researcher's `[[wikilinks]]` and `computer://` URLs get embedded in the final reply unchanged.

## Gap detection: when no skill fits

After triaging, if **none** of the five workers cleanly fits the request:

1. State that explicitly: *"None of the existing skills handle this directly."*
2. Diagnose what's missing — is it a new modality (audio? image?), a new vault (calendar? finance?), a new behavior (negotiating? translating?)?
3. **Draft a skill spec** following this template:

```markdown
**Proposed skill: <name>**

- **Purpose**: <one sentence>
- **Triggers**: <example phrases>
- **Inputs**: <what it reads — vaults, RAW, MCPs>
- **Outputs**: <what it writes — chat reply only / new vault / etc.>
- **Worker or read-only**: <write / read>
- **Boundaries**: <what it won't do; how it relates to existing skills>

Want me to build it?
```

4. **Stop** there. Don't build it without explicit approval. The Second Brain stays self-extending but the user-controlled.

## Weekly review (recurring duty)

The CEO owns one scheduled task: **Sunday 21:00 local — weekly review**.

It runs `scripts/weekly_review.py` which:
1. Reads all `Vaults/Daily/<YYYY>/<MM>/<YYYY-MM-DD>.md` notes from the past 7 days.
2. Aggregates: dominant themes, files & entities most touched, recurring open questions, what shifted vs what stalled.
3. Writes `Vaults/Daily/Weekly/<YYYY>-W<NN>.md` with the synthesis. Linked from each daily note via the connections block. Idempotent — re-running the same week appends a new "## Update at HH:MM" block.

When invoked manually ("give me my week" / "weekly review"), this skill calls the same script with `--week current`.

## Constraints

- **Never write into the vaults yourself.** Delegate writes to the worker skills (`second-brain-daily`, `second-brain-claude-archiver`, `second-brain-curator`). The CEO's own outputs go to chat, except the weekly-review file which the bundled script writes.
- **Don't bypass the workers** to do their job inline. If a request needs vault search, call the researcher — don't grep yourself. The skills exist for a reason; using them keeps behavior consistent.
- **Always confirm plans by default.** The user picked "plan-first" explicitly. Skipping confirmation is a violation of that choice unless the plan is truly trivial.
- **Don't fabricate which worker found what.** If you delegate to the researcher and it returns nothing, say so — don't invent a citation to cover the gap.
- **One coherent reply.** When you synthesize, deliver a single reply. Don't paste raw worker outputs end-to-end.

## Reference files

- `references/skills-inventory.md` — full triage matrix, example routings, edge cases.
- `references/weekly-review-template.md` — the format for `Vaults/Daily/Weekly/<YYYY>-W<NN>.md`.

## Helper script

- `scripts/weekly_review.py` — generates the weekly digest from daily notes. CLI:
  ```bash
  python3 weekly_review.py current               # this week's digest
  python3 weekly_review.py week 2026-W19         # specific ISO week
  python3 weekly_review.py path 2026-W19         # just print the target path
  ```
  Idempotent. Reads `Vaults/Daily/<YYYY>/<MM>/*.md` for the relevant date range.
