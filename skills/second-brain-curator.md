---
name: second-brain-curator
description: Convert a folder of raw files into a curated Obsidian vault with source stubs, extracted markdown notes, suggested-concept wikilinks, a Map of Content, and a color-grouped graph view. Use this skill any time the user mentions a folder under their Second Brain `RAW /` directory and wants it turned into a vault, indexed, "curated", "linked", "Obsidian-ified", "graphified", or otherwise made browsable in Obsidian — even if they don't say "skill" or "auto-curate". Triggers include phrases like "process the new folder X", "build a vault from RAW/X", "turn this raw folder into Obsidian", "add Y to my second brain", "convert these PDFs to a vault", "make MD files from this folder with all the links". The skill is rule-based (no LLM calls), works on PDFs, .docx, .txt, .md, images, CAD, code, and binary files, and is idempotent — safe to re-run on the same folder.
---

# Second Brain Curator

Converts `RAW /<folder>/` → `Vaults/<folder>/` end-to-end with a single bundled script.

## When to use this skill

Trigger this whenever the user is working with their `Second Brain` directory at `/path/to/your/second-brain/` and wants a new RAW folder processed into a curated Obsidian vault. The user might phrase the request many ways:

- "Process the new folder `<name>` in RAW"
- "Curate the `<name>` folder"
- "Build a vault for `<name>`"
- "Convert these PDFs into Obsidian notes"
- "Add this to my second brain with all the links"
- "I just dropped `<name>` in RAW, can you handle it"

If the user references a folder name and Second Brain in the same breath, this skill almost certainly applies.

## What the skill does

For a folder under `RAW /<name>/`, it produces `Vaults/<name>/` containing:

1. **`_Sources/<file>.md`** — one stub per file in the RAW folder. Frontmatter contains `raw_path` (absolute macOS path), `raw_path_uri` (clickable file:// URL), `kind`, `extension`, `size_bytes`, and a `source` tag. This is the **agent contract** — any future agent reading the stub can access the raw file via `raw_path`.
2. **`Notes/<file>.md`** — full text extracted from PDFs, .docx, .txt, .md. Includes a "Suggested concepts" section of capitalized phrases as `[[wikilinks]]` so Obsidian's graph view comes alive immediately.
3. **`_Index.md`** — Map of Content with stats (note count, source count, total size, kinds breakdown, categories).
4. **`.obsidian/graph.json`** — color-grouped: 🟥 `_Sources/`, 🟦 `Notes/`, 🟧 `tag:#extracted`.
5. **Cross-vault hint** — prints filename matches in other vaults (informational, not auto-edited).

## How to invoke

The bundled script is `scripts/auto_curate_folder.py`. Run it from anywhere with Python 3:

```bash
python3 <skill-path>/scripts/auto_curate_folder.py "<folder name>"
```

The folder name is the literal directory name under `RAW /` (the trailing space in `RAW ` is part of the user's actual path — the script handles it). Quote folder names that contain spaces.

### Common invocations

```bash
# Default — fresh folder, no Notes existed yet:
python3 scripts/auto_curate_folder.py "Bret and Rachel"

# Re-curate after the source PDFs changed (forces Notes/ regeneration):
python3 scripts/auto_curate_folder.py "Bret and Rachel" --overwrite-notes

# Custom vault name (different from folder name):
python3 scripts/auto_curate_folder.py "Recibos verdes" --vault-name "Receipts 2026"

# Custom paths (e.g., running on a different machine):
python3 scripts/auto_curate_folder.py \
    --raw "/some/RAW" \
    --vaults "/some/Vaults" \
    "Folder Name"
```

### Dependencies

The script needs four Python packages for text extraction. If a run errors out with `ModuleNotFoundError`, install them first:

```bash
pip3 install pdfplumber pypdf python-docx openpyxl
```

(In the user's Cowork sandbox these are already available; on their Mac they may need installing once.)

## Workflow once the skill is triggered

1. **Confirm the folder** — read what the user said and identify the RAW subfolder. If ambiguous, ask which folder under `RAW /` they mean.
2. **Run the script** with the folder name as the only required argument. Capture stdout — the script prints stats and any cross-vault hints at the end.
3. **Report back briefly** — number of source stubs, number of extracted notes, link to the new `_Index.md` via a `computer://` URL, and any cross-vault hints worth surfacing.
4. **If the user wants richer linking after seeing the result**, do *not* edit the script in-place. Instead, follow up with one of:
   - Move/rename specific notes manually for the user
   - Inject additional cross-links by ad-hoc Python (the marker-block convention is documented in `references/vault-conventions.md`)
   - Suggest the optional `--use-ai` upgrade (not yet implemented; the user has been told this is on the table)

## Idempotency

Re-running on the same folder is safe and expected:

- `_Sources/` stubs are unconditionally overwritten with current file metadata.
- `_Index.md` and `.obsidian/graph.json` are overwritten.
- `Notes/` are **preserved** by default to keep any human edits the user made. Pass `--overwrite-notes` to force regeneration.

## Reference files

- `references/vault-conventions.md` — describes the source-stub schema, marker-block conventions (`<!-- sources:start -->`, `<!-- connections:start -->`), and color-group queries that the broader Second Brain shares. Read this when extending the skill or when the user asks how vaults link together.

## What the skill does NOT do

- **Cross-file semantic concept extraction** (the rich Graphify-style relationships like `[[X]] - 'type_of' [[Y]]`). Suggested concepts are per-file capitalized-phrase candidates, not cross-file relations. If the user wants this, build a follow-up that calls the Anthropic API; do not jam it into this skill.
- **Routing into existing vaults**. By design every new folder becomes its own vault. If the user wants merging into DreamWorks/Zigurat/ChatGPT/Google Data, do that as a separate edit after the vault is built.
- **OCR / image-content extraction**. Source stubs are created for images but no text is pulled.
- **Watching the filesystem**. The skill is invoked on demand. For continuous watching, the user should set up a `launchd` plist or a Cowork scheduled task — this skill is the building block such automation calls into.

## Troubleshooting

- **"RAW folder not found"** — confirm the spelling (note the trailing space in `RAW `) and that the folder lives directly under `RAW /`, not nested.
- **Notes/ not regenerating after fixing a typo in the source** — pass `--overwrite-notes`.
- **Filename collisions in `_Sources/`** — the script disambiguates by parent folder name. If you see weird `(2)` `(3)` suffixes, check whether the original RAW filenames already had them; if not, file an issue and re-run with `--overwrite-notes`.
