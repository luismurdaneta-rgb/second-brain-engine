# Architecture

## Design goals

1. **Local-first & private.** Everything runs on the user's machine. Markdown is
   the only interchange format, so the data stays portable and inspectable.
2. **Deterministic core, agentic edges.** Building the vault requires no LLM
   calls — it's rule-based, fast, free, and idempotent. LLM reasoning is layered
   on top through the `skills/` specs, where it adds real value (synthesis,
   pushback, prioritization) rather than doing mechanical parsing.
3. **Readable by both humans and agents.** Every generated note carries YAML
   frontmatter. The key field is `raw_path`, which lets any downstream agent
   open the original source file on demand — this is the retrieval contract.

## The three layers

### 1. Ingestion

`auto_curate_folder.py` is the heart of the system. Given a folder of raw files
it produces, for each file:

- `_Sources/<file>.md` — a stub note with frontmatter (`raw_path`,
  `raw_path_uri`, `kind`, `size`). This is the **agent contract**: a future
  chat session reads `raw_path` and accesses the underlying file directly.
- `Notes/<file>.md` — full text extracted from PDFs / Word / spreadsheets, with
  a **Suggested concepts** section of capitalized phrases turned into
  `[[wikilinks]]` so new concept notes spawn automatically.
- `_Index.md` — a Map of Content with stats, source kinds, and categories.
- `.obsidian/graph.json` — a color-grouped graph configuration.

Source-specific ingesters normalize other inputs into the same shape:
`gmail_sync.py`, `whatsapp_sync.py`, `calendar_sync.py`,
`process_claude_export.py`, and `curate_google_drive.py`.

`raw_watcher.py` detects which RAW folders are new or stale and dispatches each
to the right ingester (`curator_routing.json` maps folder-name patterns to
destination vaults).

### 2. Storage — the vault

A plain directory of Markdown files that Obsidian renders as a linked graph.
Because the format is just files + frontmatter + wikilinks, it works with any
tool and is trivially greppable. `lib_vault.py` centralizes path resolution,
frontmatter read/write, and safe file writes so every script shares one
implementation.

### 3. Reasoning — agents & skills

Scripts that turn the corpus into signal:

- `morning_brief.py` / `weekly_review.py` — digests of what needs attention,
  including unanswered email threads (tracks who replied last).
- `extract_action_items.py` — mines commitments and promised replies.
- `blind_spots_gather.py` / `navigator_gather.py` — proactively surface dropped
  goals, contradictions, and movement (or lack of it) toward stated objectives.
- `situation_data.py` (+ `situation_dashboard.template.html`) — assembles an
  attention dashboard.
- `vault_architect_audit.py` — graph-health audit (orphans, broken wikilinks,
  link density, frontmatter coverage, near-empty notes).
- `clean_vault.py` — conservative, reversible noise sweeper (quarantine, never
  delete).

The `skills/` directory holds the LLM-facing counterparts: each is a
self-contained spec describing when to trigger and how to behave, so a
compatible agent runtime can route a natural-language request to the right
worker.

## Key decisions & trade-offs

- **Rule-based extraction over LLM extraction** — chosen for speed, zero cost,
  determinism, and privacy. The cost is less "understanding" during ingestion;
  that's deferred to the reasoning layer where it's cheaper to be selective.
- **Markdown + frontmatter as the contract** — chosen for portability and
  agent-readability over a database. The cost is no rich querying; mitigated by
  the graph, the Map of Content, and `raw_path` pointers.
- **Idempotency everywhere** — every ingester is safe to re-run; digests and
  daily notes append rather than overwrite. This makes scheduled automation
  safe and debuggable.
- **Reversible cleanup** — the cleaner quarantines instead of deleting, because
  false positives on personal data are unacceptable.

## Running under automation

The ingesters and digest scripts are designed to be driven by scheduled tasks
(e.g. a nightly "build daily note," a 4-hourly Gmail pull, a morning brief).
State files (kept out of git via `.gitignore`) track what has already been
processed so re-runs are incremental.
