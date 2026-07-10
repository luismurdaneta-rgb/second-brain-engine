---
name: second-brain-situation-explainer
description: |
  Build an on-demand dashboard artifact that aggregates every demand on your attention into a Cowork artifact: Eisenhower importance×urgency 2×2, today's calendar, inbox-needs-reply, threads waiting on others, backlog-age histogram, stakeholder-load chart, open commitments. Reads vault state at /path/to/your/second-brain/Vaults/.
  
  Trigger when the user says: "situation explainer", "give me my dashboard", "what should I focus on", "where should I put my attention", "attention map", "what's on fire", "show me my situation", "build me a dashboard", "status of things", "what's on my plate", or any similar request for a visual snapshot of current attention demands.
  
  Do NOT trigger for narrow lookups ("who is X", "what did I email Y") — that's researcher. Do NOT trigger for daily-note ops — that's daily. Do NOT trigger for sparring on a decision — that's thought-partner.
  
  Read-only on the vault; writes only an HTML artifact.
version: 0.1.0
---

# Situation Explainer

When invoked, build a fresh artifact called `situation-explainer` containing a
dashboard of every current demand on your attention.

## How it works

1. **Build the snapshot.** Run the data extractor:

   ```bash
   python3 "/path/to/your/second-brain/_scripts/situation_data.py" \
     --vault-root "/path/to/your/second-brain/Vaults" \
     --pretty \
     --output /tmp/situation_<unix-timestamp>.json
   ```

   This walks `Vaults/Google Data/Threads/`, `Vaults/Google Data/Calendar/`,
   and `Vaults/Daily/` and emits JSON of all attention items with heuristic
   importance × urgency scores (1–5 each, family-first weighting).

2. **Render the dashboard.** Read the template at
   `/path/to/your/second-brain/_scripts/situation_dashboard.template.html`
   (or copy the canonical version from the artifact you most recently built),
   substitute the `__SNAPSHOT__` placeholder with the JSON contents, and write
   the result to `/tmp/situation_<unix-timestamp>.html`.

3. **Publish the artifact.** Call `mcp__cowork__create_artifact` with:

   ```
   id: "situation-explainer-<YYYY-MM-DD-HHMM>"
   html_path: "/tmp/situation_<unix-timestamp>.html"
   description: "Snapshot of your attention demands from <date> — items by
                 Eisenhower quadrant, calendar today, inbox-needs-reply, ..."
   ```

   The user picked "always rebuild fresh" — each invocation creates a new
   artifact rather than updating the previous one. Older versions stay in
   the artifact list for comparison.

4. **Reply briefly in chat** with a 2–3 sentence summary highlighting Q1
   items and any urgent RSVPs. Do NOT paste full lists into chat — point at
   the artifact.

## Importance / urgency model

The script applies these rules:

- **Importance 5:** family (REDACTED, REDACTED, REDACTED, co-parenting), legal
- **Importance 4:** academic (TFM, Zigurat, Tatiana, Canvas), health
- **Importance 3:** work (DreamWorks, Archistar, Autodesk, Revit), real-human admin
- **Importance 2:** routine admin (bills, tax, social security)
- **Importance 1:** bot mail / OTPs / automated notifications

- **Urgency 5:** today
- **Urgency 4:** within 2 days, OR an event with `needsAction` RSVP
- **Urgency 3:** within 7 days
- **Urgency 2:** within 14 days, or aged 1–6 days without deadline
- **Urgency 1:** stale or no deadline

Quadrants follow the Eisenhower matrix: Q1 = both ≥4, Q2 = importance ≥4 only,
Q3 = urgency ≥4 only, Q4 = neither.

## Boundaries

- **Read-only on the vault.** This skill never writes vault files; it only
  publishes an HTML artifact.
- **Items close automatically.** When the user replies to an email, the thread
  no longer appears in "needs reply." When a daily-note bullet is removed,
  the commitment falls off. There is no manual "done" mechanism — the user
  picked auto-only closing.
- **No sensitive-content filtering.** All real demands are surfaced
  (including the REDACTED / REDACTED / REDACTED thread). the user is the only viewer.
- **Don't synthesize beyond what the data shows.** The "Top of mind" line at
  the top of the artifact is built from the highest-priority Q1 item; don't
  invent narrative context.

## Tuning the heuristics

The keyword lists live in `_scripts/situation_data.py` under
`FAMILY_TOKENS`, `ACADEMIC_TOKENS`, `WORK_TOKENS`, etc. To add a new
domain (e.g. health-related contacts) or correct a misclassification, edit
that file directly — the next invocation picks up the new rules.

## Helper assets

- **Data extractor:** `_scripts/situation_data.py` — emits the JSON snapshot.
- **Dashboard template:** the most recently built artifact's HTML serves as
  the template (it self-contained, light-mode, Chart.js v4 from the
  jsdelivr CDN allowlist). To bootstrap a clean template,
  re-build the artifact once and copy its HTML.
