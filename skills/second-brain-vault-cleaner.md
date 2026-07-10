---
name: second-brain-vault-cleaner
description: Conservative noise sweeper for your Second Brain vaults. Flags three categories ONLY when rules are confident — marketing/promo emails (bulk-mail domains, Gmail CATEGORY_PROMOTIONS, noreply+unsubscribe), bot notifications (OTP/verify subjects, GitHub/Linear/Slack/calendar bot senders, transactional retail), and empty or near-empty notes. Moves flagged files into each vault's _Quarantine/auto-cleaner/date folder (reversible, never deletes). Trigger when the user says "clean up the vault", "clean my vault", "purge the noise", "kill the noise", "remove spam", "trim the fat", "declutter Obsidian", "vault hygiene", "find junk in my vault", "what's clearly noise", "quarantine spam", "tidy up Second Brain", or "get rid of the obvious junk". Do NOT trigger for building a new vault from RAW (second-brain-curator), retrieval (second-brain-researcher), or deleting a single named file. Default is dry-run with a report; apply only after the user confirms.
version: 0.1.0
---

# Second Brain Vault Cleaner

Sweeps every vault under `/path/to/your/second-brain/Vaults/` for clear noise and moves it into a per-vault quarantine folder. Read-only by default — produces a report. Acts only when invoked with `--apply`.

## When to use this skill

Trigger on any phrasing that signals "the vault has too much junk in it, get rid of the obvious stuff":

- "clean up the vault", "clean my vault", "vault hygiene"
- "purge / kill / remove the noise"
- "get rid of the obvious junk", "trim the fat", "declutter"
- "quarantine the spam / promos / bot mail"
- "find what's clearly noise"

If the user wants ambiguous items reviewed too, that's a different conversation — this skill is deliberately conservative and skips anything it isn't sure about.

## What counts as "clear noise"

Three categories the rules are confident about. Everything else is left alone.

### 1. Marketing / promo emails (`marketing-promo`)
- Sender domain is a known bulk-mail provider (mailchimp, sendgrid, mailgun, hubspot, marketo, sendinblue, klaviyo, amazon-ses, intercom, customer.io, etc.) or a known marketing subdomain (`e.uber.com`, `e.airbnb`, `mailer.linkedin`, etc.).
- Sender address local-part is one of `noreply / no-reply / news / newsletter / marketing / promo / deals / offers / unsubscribe / mailings / notifications` AND the body contains `unsubscribe`.
- Gmail label frontmatter includes `CATEGORY_PROMOTIONS`, `CATEGORY_SOCIAL`, or `CATEGORY_FORUMS` and the file is outside `_Quarantine/`.

### 2. Bot / automated notifications (`bot-notification`)
- Subject matches an OTP / verification / password-reset / magic-link / 2FA / sign-in-code pattern.
- Sender domain matches a known bot/automation source (`github.com`, `linear.app`, `slack.com`, `notion.so`, `figma.com`, `circleci.com`, `pagerduty.com`, `datadoghq.com`, `sentry.io`, `noreply.youtube.com`, `facebookmail.com`, etc.) AND the body is short (< 600 chars).
- Subject is a calendar invite (`Invitation:`, `Reminder:`, `Canceled event:`, `Updated invitation:`) with a near-empty body.
- Subject is transactional (`Your order …`, `Order confirmation`, `Shipping update`, `Payment received`, `Receipt from …`).
- Subject has a bot prefix (`[GitHub]`, `[Linear]`, `[Slack]`, `[CI]`, `[Build]`) with a short body.

### 3. Empty or near-empty notes (`empty-near-empty`)
Computed on body content with frontmatter, marker blocks (`<!-- sources:start -->`, `<!-- connections:start -->`), `[Open in macOS]` lines, and pure whitespace stripped:
- Body length < 50 chars AND zero wikilinks AND zero meaningful tags.
- For frontmatter `kind: chat | voicenote | transcript`, threshold is < 30 chars.
- DEFERS if the connections marker block contains real wikilinks (someone has already linked it into the graph — assume value).

## What is NEVER touched

Even if rules would match, these are skipped unconditionally:

- Anything under `_Sources/`, `_Topics/`, `_Communities/`, `_Meta/`, `5_Meta/`, `Contacts/`, `Threads/`, `Topics/`, `.obsidian/`, `_Quarantine/`, `_archive_chatgpt/`, `Daily/`.
- Files named `_Index.md`, `README.md`, `MOC.md`, `Map of Content.md`.
- Notes with frontmatter `pinned: true` or `noise: false`.
- Notes whose `connections:` block contains real wikilinks.

## How to invoke

The bundled script is `scripts/clean_vault.py`. Stdlib-only Python 3, no extra deps.

```bash
# Default — dry run, all vaults, all reasons, report written to _scripts/cleaner-report-<date>.md
python3 scripts/clean_vault.py

# Apply — quarantine flagged files
python3 scripts/clean_vault.py --apply

# One vault only
python3 scripts/clean_vault.py --vault "Google Data"

# Limit categories (skip the empty-near-empty rule on first pass)
python3 scripts/clean_vault.py --reasons marketing-promo,bot-notification

# Safety cap on first apply
python3 scripts/clean_vault.py --apply --max 200

# Custom paths
python3 scripts/clean_vault.py --vaults-root "/some/Vaults" --report-dir "/tmp"
```

## Workflow once the skill is triggered

1. **Run dry-run first** — `python3 _scripts/clean_vault.py`. The report is also written to `Second Brain/_scripts/cleaner-report-<date>.md` so the user can browse it inside Obsidian.
2. **Surface the plan in chat** — per-vault counts by reason, with 5 example file paths per category.
3. **Ask the user** — "Apply this and quarantine? (yes / scope it down / no)". If he says scope it down, re-run with `--vault X` or `--reasons R1,R2` or `--max N`.
4. **Apply on confirmation** — `--apply`. Each file is moved to `<vault>/_Quarantine/auto-cleaner/<YYYY-MM-DD>/<original-rel-path>` with a manifest CSV recording reason + evidence.
5. **Report briefly** — number moved per vault and a `computer://` link to the quarantine folder so the user can review.

## Idempotency & reversibility

- Re-running `--apply` is safe. Files already at the quarantine destination are skipped.
- Nothing is ever deleted. Reverse a quarantine by moving the file out of `_Quarantine/auto-cleaner/`.
- Each apply writes a `_manifest.csv` next to the moved files with `original_rel_path, reason, evidence, quarantined_at`. Months later it's still clear why each file was flagged.

## What the skill does NOT do

- Does NOT delete. Quarantine only.
- Does NOT touch RAW. Read-only on RAW; only moves files inside Vaults.
- Does NOT classify ambiguous mail. Conservative by design — the user will keep some borderline noise.
- Does NOT update wikilinks pointing to quarantined notes. Stale links surface in Obsidian's "Unresolved links" pane; the user decides per case.
- Does NOT regenerate `_Index.md` counts. Slightly stale until the next `curate_*.py` run.
- Does NOT call any LLM. Pure rules.

## Troubleshooting

- **"Wait, that wasn't noise"** — move the file back from `_Quarantine/auto-cleaner/<date>/<rel-path>` to where it was. Add `pinned: true` to its frontmatter so the cleaner respects it on future runs.
- **Too aggressive on empty notes** — `--reasons marketing-promo,bot-notification` to skip the empty-near-empty rule.
- **Too conservative — clearly noise stayed** — paste 2–3 example file paths into chat. Either (a) the file is in a protected location, (b) the sender pattern isn't in the list yet, or (c) it has a populated connections block making the rule defer. Pattern lists in `clean_vault.py` (`MARKETING_LOCAL_PARTS`, `BULK_DOMAINS`, `BULK_DOMAIN_SUBSTRINGS`, `BOT_DOMAIN_SUBSTRINGS`, `OTP_PATTERNS`) are designed to be extended in place.
- **Quarantine got huge** — once the user has reviewed, delete `_Quarantine/auto-cleaner/<date>/` outright. The manifest tells him what was in it.

## Files in this skill

- `SKILL.md` — this file.
- `scripts/clean_vault.py` — the cleaner. Single self-contained Python script, stdlib-only.
