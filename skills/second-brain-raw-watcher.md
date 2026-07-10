---
name: second-brain-raw-watcher
description: |
  Scan your Second Brain `RAW /` directory, detect which folders haven't yet been turned into vaults (or whose RAW files are newer than the corresponding vault), and dispatch each unprocessed folder to the right curator: `whatsapp_sync.py` for "Whatsapp", `convert.py` for "Google Takeout", `process_claude_export.py` for "Claude", and `auto_curate_folder.py` for everything else. Reports loose files at the RAW root that need to be grouped into a folder before they can be curated.

  Trigger when the user says: "scan RAW", "check for new folders in RAW", "what hasn't been processed", "curate any new RAW folders", "watch RAW", "process RAW", "what's new in RAW", "anything new to vault", or any similar phrasing about catching up the vault with RAW.

  Do NOT trigger for narrow "process this specific folder" requests — that's the curator skill called directly. Do NOT trigger for retrieval — that's researcher.

  Default mode is dry-run. Apply only when the user confirms.
version: 0.1.0
---

# RAW Watcher

When invoked, scan the `RAW /` directory for folders that need curation, show
the plan, then execute on confirmation.

## How it works

1. **Dry-run first.** Run:
   ```bash
   python3 "/path/to/your/second-brain/_scripts/raw_watcher.py"
   ```
   This walks every direct subdirectory of `RAW /` and classifies each as:
   - **processed** ✅ — the corresponding vault exists and is fresher than RAW
   - **stale** 🔄 — the vault exists but RAW has newer files (re-curate suggested)
   - **new** 🆕 — no vault yet; needs first-time curation

2. **Show the plan in chat.** List each folder with its status and the
   command that would run. For folders mapped to special curators
   (Whatsapp, Google Takeout, Claude), name the dispatch.

3. **Ask the user to confirm,** then run with `--apply`:
   ```bash
   python3 "/path/to/your/second-brain/_scripts/raw_watcher.py" --apply
   ```
   Or, to limit to one folder:
   ```bash
   python3 "/path/to/your/second-brain/_scripts/raw_watcher.py" --apply --only "Folder Name"
   ```

4. **Report briefly** in chat: counts (N new, M stale processed) and any failures.

## Routing rules

| RAW folder            | Dispatched to                                |
|-----------------------|----------------------------------------------|
| `Whatsapp`            | `_scripts/whatsapp_sync.py`                  |
| `Google Takeout`      | manual — print "re-run convert.py"           |
| `Claude`              | `_scripts/process_claude_export.py`          |
| any other folder      | `_scripts/auto_curate_folder.py "<name>"`    |
| loose files at RAW root | flagged; user must group into a folder    |

The map lives at the top of `raw_watcher.py` (`SPECIAL_CURATORS`) and can be
extended in place.

## What the curator now handles

`auto_curate_folder.py` extracts text into `Notes/<file>.md` for these types:

- **PDF** — page-by-page text via pdfplumber / pypdf
- **DOCX/DOC** — paragraph text via python-docx
- **TXT/MD** — straight read
- **IPYNB** — cell-by-cell extraction (markdown cells as prose, code cells as fenced blocks)
- **CSV/TSV** — first 50 rows as a markdown table, with a "preview only" footer if larger
- **XLSX/XLS** — each sheet as a markdown table (first 30 rows per sheet)
- **JSON** — pretty-printed (capped at 8000 chars)
- **PY/JS/TS** — fenced code block in the Notes/ file
- **YAML** — fenced code block

Source-only types (no Notes extraction, just a `_Sources/` stub):
images, video, audio, CAD (dwg/rvt/rfa/ifc), zip, opaque binary.

Re-run with `--overwrite-notes` to force regeneration.

## Boundaries

- **Read-only on RAW.** The watcher never writes into RAW.
- **Doesn't move loose files.** It surfaces them; the user decides where they
  belong.
- **Doesn't touch already-processed folders by default.** Stale ones are
  flagged but not auto-recurated unless `--apply` is passed AND the user
  approves the plan that includes them.
- **Doesn't recurse into subfolders to find sub-vaults.** Each direct child of
  RAW is a candidate; deeper structure is the curator's job.

## Helper scripts in this bundle

- `scripts/raw_watcher.py` — the watcher itself.
- `scripts/auto_curate_folder.py` — the generic curator (also available
  standalone in `_scripts/`).
