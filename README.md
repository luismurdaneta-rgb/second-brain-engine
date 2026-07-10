# Second Brain Engine

A local-first, agent-friendly toolkit that turns a messy folder of raw files
(PDFs, Word docs, chat exports, emails, spreadsheets, images) into a fully
linked [Obsidian](https://obsidian.md) knowledge vault — and then runs a suite
of retrieval, summarization, and "what am I missing?" agents on top of it.

Everything runs on your own machine. No documents leave your computer.

> **Note on data:** this repository contains the *engine* only — the Python
> pipeline, the skill specs, and a tiny synthetic sample. My personal vault and
> raw files are **not** included and never will be. Point the scripts at your
> own folder to use it.

---

## Why this exists

I had years of files scattered across Google Drive, WhatsApp exports, ChatGPT
and Claude history, email, and course material. Search alone doesn't help when
you don't remember what you're looking for. I wanted a system that could:

1. **Ingest** anything and normalize it into plain Markdown.
2. **Link** it automatically so related ideas surface in a graph.
3. **Retrieve** across all of it, and stay **readable by an LLM agent** so a
   chat assistant can answer questions grounded in my own history.
4. **Notice things for me** — dropped goals, unanswered threads, this week's
   priorities — instead of me having to ask.

The result is ~9,000 lines of Python across 21 scripts plus 13 agent "skills."

## What a reviewer is looking at

| Area | Where | What it demonstrates |
|------|-------|----------------------|
| Ingestion pipeline | `scripts/auto_curate_folder.py`, `gmail_sync.py`, `whatsapp_sync.py`, `calendar_sync.py`, `process_claude_export.py`, `curate_google_drive.py` | Robust parsing of heterogeneous, messy real-world formats; idempotent re-runs |
| The "agent contract" | `_Sources/*.md` frontmatter (`raw_path`) | Every note carries a machine-readable pointer back to its source file, so an agent can open the original on demand — retrieval-augmented by design |
| Retrieval + synthesis | `scripts/morning_brief.py`, `weekly_review.py`, `extract_action_items.py`, `situation_data.py` | Turning a corpus into signal: action items, unanswered threads, priorities |
| Proactive agents | `scripts/blind_spots_gather.py`, `navigator_gather.py` + `skills/` | Goal tracking, contradiction hunting, devil's-advocate analysis |
| Self-auditing | `scripts/vault_architect_audit.py`, `clean_vault.py` | Graph health checks: orphans, broken links, near-empty notes, noise removal |
| Shared design | `scripts/lib_vault.py` | One helper module for path handling, frontmatter, and safe writes |

## Architecture at a glance

```
        RAW files                     Curated vault                 Agents
   ┌──────────────────┐        ┌────────────────────────┐    ┌──────────────────┐
   │ PDF · DOCX · XLSX │        │ _Sources/*.md  (stub + │    │ morning_brief    │
   │ chat exports      │  ───▶  │   raw_path frontmatter)│──▶ │ weekly_review    │
   │ email · images    │curate  │ Notes/*.md  (full text │    │ blind_spots      │
   │ code · CAD        │        │   + [[wikilinks]])     │    │ navigator        │
   └──────────────────┘        │ _Index.md  (Map of     │    │ situation dash   │
                               │   Content) + graph.json│    │ architect audit  │
                               └────────────────────────┘    └──────────────────┘
```

The pipeline is **rule-based and deterministic** (no LLM calls needed to build
the vault), which makes it fast, free to run, and idempotent — safe to re-run
on the same folder. The `skills/` specs describe how an LLM agent layers on top
for the reasoning tasks.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design.

## Quickstart

```bash
# 1. install extraction deps
pip install -r requirements.txt

# 2. curate the included synthetic sample into an Obsidian-ready vault
python3 scripts/auto_curate_folder.py \
  --raw   examples/sample_raw \
  --vaults /tmp/demo_vault \
  "Project Falcon"

# 3. open /tmp/demo_vault in Obsidian and explore the graph
```

Configuration is via CLI flags or environment variables — no hardcoded paths:

```bash
export SECOND_BRAIN_BASE="/path/to/your/second-brain"   # root that holds RAW/ and Vaults/
```

## The skills layer

`skills/` holds 13 self-contained agent specifications (Markdown with
frontmatter) that a compatible LLM runtime can load. Each is a focused worker:

- **curator / raw-watcher** — build and keep vaults in sync with RAW
- **researcher** — answer questions grounded in the vault
- **daily / archiver** — capture sessions into dated notes
- **architect / vault-cleaner** — audit structure and sweep noise
- **blind-spots / navigator / thought-partner** — proactive reasoning and pushback
- **situation-explainer / action-items / ceo** — prioritization and orchestration

## Tech

Python 3.10+ · `pdfplumber` · `pypdf` · `python-docx` · `openpyxl` · Obsidian
graph JSON · Markdown/frontmatter as the interchange format.

## License

MIT — see [LICENSE](LICENSE).
