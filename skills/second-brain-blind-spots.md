---
name: second-brain-blind-spots
description: Proactively hunt your Second Brain for things he's MISSING — opportunities he hasn't pursued, goals stated and dropped, contradictions and stakeholder blind spots, recurring themes he hasn't named. Devil's-advocate voice, push hard, find holes, no coddling. Distinct from thought-partner (which spars on a specific decision he brings); this skill is proactive recon that surfaces what he didn't know to ask. Default scope is all vaults all time, tiered depth (fast default, --deep flag for full sweep). Each run produces ranked chat findings, a dated markdown note under Vaults/Daily/Blind-spots/, and an interactive Cowork artifact. Trigger phrases: "what am I missing", "find my blind spots", "blind spots", "what opportunities am I ignoring", "play devil's advocate on my vault", "challenge me on my goals", "what goals have I dropped", "what have I forgotten". Do NOT trigger for sparring on a single named decision (use thought-partner) or for retrieval (use second-brain-researcher).
version: 0.1.0
---

# Second Brain Blind Spots

Proactive recon over your vault. Finds what he's missing across four lenses:

1. **Opportunities not pursued** — people/projects/ideas that keep showing up in his notes but he's never acted on.
2. **Goals stated and dropped** — intentions in daily notes that went quiet; half-started threads.
3. **Contradictions, blind spots, counter-evidence** — past-vs-present disagreements; stakeholder coverage gaps; saved-but-not-integrated counter-evidence.
4. **Recurring themes he hasn't named** — patterns appearing in different contexts without a through-line yet drawn.

Voice: **devil's advocate**. Push hard, name holes, don't soften. Cite evidence from his own files — that's what makes the pushback land instead of feeling generic.

This skill is *different from* `thought-partner`. Thought-partner sparring-partners a single decision the user brings to the table. Blind-spots goes hunting on its own.

## When to trigger

Yes:
- "What am I missing?"
- "Find my blind spots."
- "What opportunities am I ignoring?"
- "What goals have I dropped?"
- "Play devil's advocate on my vault."
- "Where am I asleep?"
- "Scan my vault for what I'm dropping."
- "Challenge me on my goals."

No:
- "What do you think about doing X?" → `thought-partner` (specific decision).
- "Who is X?" / "Find me the contract about Y" → `second-brain-researcher`.
- "Clean up the vault" → `second-brain-vault-cleaner`.

## Two-stage architecture

The skill is rule-gather + LLM-synthesize:

**Stage 1 — gather (mechanical).** `scripts/blind_spots_gather.py` walks the vault and produces a JSON digest with signals:
- `top_entities` — frequently-linked entities, with `has_own_note: false` flagged as candidates for "opportunity not pursued"
- `stated_goals` — intentions parsed from Daily notes that haven't reappeared in subsequent daily notes
- `stale_projects` — project root notes not modified in 120+ days
- `quiet_contacts` — contacts with high historical mention count who haven't been mentioned in 120+ days
- `frustration_phrases` — entities co-occurring with frustration vocabulary near them
- `weekly_lookahead` — items from old Weekly review "looking ahead" sections that don't show up in subsequent daily notes
- `dropped_threads` — Threads/ notes silent for 60+ days
- `sample_notes` — curated list of file paths the synthesizer should read for richer context

**Stage 2 — synthesize (you, Claude, reading the digest + sampled notes).** Read the digest, sample 15–25 notes from `sample_notes`, then produce the three outputs.

## Invocation

```bash
# Default — fast tier (MOCs, project roots, recent Daily, Weekly, Contacts, Threads)
python3 _scripts/blind_spots_gather.py

# Deep — full vault sweep
python3 _scripts/blind_spots_gather.py --deep

# Custom out path
python3 _scripts/blind_spots_gather.py --out /tmp/digest.json
```

The fast tier finishes in seconds for most vaults. The deep tier takes longer but catches signals the index-only pass misses. Default to fast unless the user asks for deep or the fast pass returns suspiciously few signals.

## Workflow

1. **Run the gather script.** Capture the JSON path it printed.
2. **Read the JSON.** Don't try to summarize the whole vault from scratch — the script has already filtered to ~150 candidate signals across the 6 categories.
3. **Sample 15–25 notes from `sample_notes`.** Read enough to ground specific claims with specific evidence. Prefer recent Daily + stale projects + quiet contacts.
4. **Synthesize 5–10 ranked findings.** Each finding must:
   - Belong to one of the four lenses (opportunity / dropped goal / contradiction-or-blind-spot / unnamed theme).
   - Cite specific evidence (file path + brief quote or date).
   - End with a **sharp, uncomfortable question** that forces the user to confront the gap.
   - Avoid generic advice. If the pushback would fit any random user, throw it out.
5. **Produce the three outputs** (chat + note + artifact). Template below.
6. **Update memory** with the date and the IDs of findings surfaced, so the next run can de-duplicate.

## Output 1 — chat reply

5–10 ranked findings. Each:

```
**N. [Lens] One-line headline.**
Evidence: `path/to/file.md` (date) — "brief quote or fact".
Sharp question: "What's stopping you from <action>?" or "Why hasn't <X> happened?"
```

Lead with the strongest 2–3. Order by confidence × consequence, not by category. End the chat reply with a single closing line — no postamble, no apology, no "let me know if…". Just the last finding.

## Output 2 — markdown note in the vault

Path: `Vaults/Daily/Blind-spots/<YYYY-MM-DD>-blind-spots.md`. Idempotent — if it exists, append a new "Update at HH:MM" section rather than overwriting.

Template:

```markdown
---
type: blind-spots
date: <YYYY-MM-DD>
depth: fast|deep
voice: devil's-advocate
---

# Blind spots — <YYYY-MM-DD>

> Proactive recon over the vault. Devil's advocate voice.

## Headline pushback
<one paragraph — the single most uncomfortable thing the digest is screaming.>

## Opportunities you're not pursuing
- **[[Entity]]** — mentioned <N> times across <vaults>, no dedicated note. Last appeared <date>. *Why hasn't this become a project?*
- ...

## Goals stated and dropped
- "<exact text>" — stated <date> in [[<daily note>]]. Hasn't reappeared in <N> days. *What changed?*
- ...

## Contradictions, blind spots, counter-evidence
- ...

## Recurring themes you haven't named
- ...

## Sharpest questions to sit with
1. ...
2. ...
3. ...

---
_Run: <YYYY-MM-DD>, depth: <fast|deep>, signals consumed: top_entities=<n>, stated_goals=<n>, stale_projects=<n>, quiet_contacts=<n>, frustration=<n>, weekly_lookahead=<n>, dropped_threads=<n>_
```

Use real wikilinks for entity/project references so they wire into the graph.

## Output 3 — interactive artifact

Use `mcp__cowork__create_artifact`. One HTML page with four columns or tabs (one per lens). Each finding card shows:

- Headline (devil's-advocate tone)
- Evidence file path (rendered as `computer://` link)
- Date / mention count / days quiet
- Sharp question
- "Dismiss" / "Act on this" buttons that just call `sendPrompt(text)` with a prefilled follow-up like `"Sit me down on blind spot #3"` or `"Why I'm dropping that — let me explain"`.

Persist dismissed/acted IDs in `localStorage` so reopening the artifact keeps state.

Keep it visually plain — this is content-heavy and the user is here to sit with the findings, not to admire the chrome.

## Style guide for the devil's-advocate voice

Do:
- Cite specific files and dates. "On 2026-02-14 you said you'd ship the BIM dashboard by end of Q2; it hasn't been mentioned since."
- Use his own words against him. Pull a phrase from his note and challenge it directly.
- Ask one specific question per finding. Not three.
- Surface asymmetry. "You spend X energy on Y; what's the return?"
- Hold the line on one thing per finding. Resist combining.

Don't:
- Generic advice ("you should focus more"). If a coach-bot could write it, cut it.
- Soft language ("you might want to consider"). Use direct phrasing: "you've abandoned X", "you haven't talked to Y in 8 months".
- Both-sides hedging on every finding. The point is to push.
- Quote-without-attribution. Every claim cites a path.
- Sermon. Findings, not lectures.

## Memory protocol

After each run, save (as a `project` memory):
- Run date
- Top 5 findings (lens, headline, evidence path, dismissed/engaged status if known)

On subsequent runs, before generating output, READ this memory and avoid re-surfacing identical findings unless evidence has changed materially. If the user dismissed a finding last week and the evidence is unchanged, don't re-raise it.

## Scheduled weekly drop

A Cowork scheduled task `second-brain-blind-spots-weekly` runs Sunday after the existing weekly-review task. It:

1. Runs `blind_spots_gather.py` (fast tier).
2. Generates the markdown note at `Vaults/Daily/Blind-spots/<YYYY-MM-DD>-blind-spots.md`.
3. Skips the chat output (no live conversation).
4. Skips the artifact (artifacts need an open Cowork session).

On-demand invocations always produce all three outputs.

## What this skill does NOT do

- Does NOT modify any vault content other than writing the dated blind-spots note. Read-only on every other file.
- Does NOT delete or quarantine anything.
- Does NOT take action on findings. It surfaces; the user decides.
- Does NOT try to detect contradictions mechanically — that's Stage 2 (synthesis) work. The script gathers signals; you do the reasoning.

## Files in this skill

- `SKILL.md` — this file.
- `scripts/blind_spots_gather.py` — mechanical signal extractor. Stdlib only.

## Troubleshooting

- **Fast tier returns very few signals** — re-run with `--deep`. Some vaults' useful content lives below the index-and-recent-Daily tier.
- **Findings feel generic** — you're skipping the "sample 15–25 notes from `sample_notes`" step. Without grounding in actual note content, the synthesis devolves to platitudes.
- **Same findings every run** — check the memory protocol. You should be de-duplicating against the prior run's surfaced findings.
- **Too soft / coddling** — re-read the style guide. Use direct phrasing, cite files, ask one specific uncomfortable question per finding.
