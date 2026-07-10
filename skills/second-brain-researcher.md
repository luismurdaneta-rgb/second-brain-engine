---
name: second-brain-researcher
description: Answer questions about your life, work, projects, contacts, conversations, files, or any other topic that lives inside the Second Brain at /path/to/your/second-brain/. Use this skill aggressively — any time the user asks "who is X", "what did I email Y about", "find me the contract for Z", "what came up in my ChatGPT history about W", "did I ever discuss V", "show me everything related to U", "what's in the DreamWorks/Zigurat/ChatGPT vault about T", "how much did I spend on S", "when did I last hear from R", or anything similar that could plausibly be answered from the user's own past correspondence, projects, notes, or files. Also trigger when the user references a person/project/topic by name without explicitly asking a question — they probably want context. Do NOT trigger for general world-knowledge questions ("what's the capital of France") or for pure coding tasks. Default to vault-first (fast, indexed) and only drill into RAW files when the vault note isn't enough.
---

# Second Brain Researcher

Search your curated Obsidian vaults at `/path/to/your/second-brain/Vaults/` and, when needed, drill into the original `RAW /` files via the `raw_path` frontmatter on `_Sources/` stubs.

## When to use this skill

Trigger on **any factual or contextual question whose answer might live in your own data**. Examples that should fire the skill:

- "who is Sarah Mendez and what was our last conversation about?"
- "find me the receipts from last quarter"
- "what was the architect_agent's system prompt?"
- "did I ever email someone about the Carvoeiro project?"
- "show me everything about my Portuguese tax stuff"
- "what concepts came up in my ChatGPT history about humanitarian work?"
- "what's the budget for Project Phoenix?"
- "when did I last hear from Bret?"
- references to people, projects, threads, or topics by name (without an explicit question)

Do **not** trigger for:
- general world-knowledge questions ("what's the capital of France")
- pure coding tasks unrelated to the Second Brain
- meta-questions about the vault structure itself (those are usually answered from memory)

## Vault landscape — where to look

| Vault | Best for |
|---|---|
| `Vaults/Google Data/` | Email history (1,214 personal + 5,588 quarantined under `Gmail/_Quarantine/`), contacts, threads, Gmail topics, YouTube history, Gemini personas |
| `Vaults/ChatGPT/` | All ChatGPT conversation transcripts (PARA-organized) + 3,291 extracted concept nodes under `Concepts/<community>/` |
| `Vaults/DreamWorks/` | BIM project, architecture renders, agent code, communities/nodes graph |
| `Vaults/Zigurat/` | Master's coursework — AI/ML, BIM, course PDFs and datasets |
| `Vaults/Recibos verdes/` | Portuguese receipts/invoices (PDFs with extracted text) |
| `Vaults/_archive_chatgpt/` | Old pre-merge ChatGPT vaults — avoid unless the user explicitly asks for archived content |

Always start by reading `_Index.md` of any vault you'll search — it has top-level stats and entry points.

## Search strategy (in order)

### 1. Triage by vault

From the user's question, identify 1–2 likely vaults. If the question mentions email, contacts, dates → Google Data. ChatGPT/AI questions → ChatGPT. Architecture/BIM/renders → DreamWorks. Coursework → Zigurat.

If unclear, search across all vaults — but stop and report findings vault-by-vault rather than concatenating noise.

### 2. Use Grep aggressively

Use the Grep tool against the vault directories. Examples:

- **Find a person:** `Grep` for the name (and email-domain variants if applicable) inside `Vaults/Google Data/Contacts/` first; then across all `.md` if no hit.
- **Find emails about X:** `Grep -l` inside `Vaults/Google Data/Gmail/` (skip `_Quarantine/` unless the user asks for marketing/automated mail).
- **Find a ChatGPT conversation:** `Grep -l` inside `Vaults/ChatGPT/0_Inbox`, `2_Areas`, `4_Archive`. The `topic:` frontmatter and PARA folders narrow scope.
- **Find a concept:** `Grep` for the term inside `Vaults/ChatGPT/Concepts/` (concept notes are short, dense with relations).

### 3. Read `_Index.md` and entity MOCs before diving into individual notes

A vault's `_Index.md` plus `_Topics/<topic>.md`, `_Communities/<community>.md`, `Contacts/<name>.md`, `Threads/<subject>.md` give you the structure of an answer in 100–500 lines instead of grepping through thousands of files. Read these first if the question maps to one.

### 4. Drill into RAW only when necessary

The vault notes often contain enough context. Read `RAW /` files only when:
- The user asks for an exact quote, full document content, or original layout
- The vault has a `_Sources/<file>.md` stub with `raw_path:` frontmatter pointing to the file
- An extracted Note is truncated (look for `…(truncated, original is N chars)`)

To read a RAW file:
1. Find the relevant `_Sources/<x>.md` stub
2. Parse its frontmatter for `raw_path:` (absolute macOS path)
3. Read that path with the file tool

The bundled `scripts/sb_search.py` handles this lookup automatically — see "Helper script" below.

### 5. Stop searching when you have enough

If you have 3–5 strong matches, synthesize an answer. Don't grep deeper just because you can. Aim for: minimum search effort that fully answers the question.

## Citation format

Every factual claim must be traceable. Use this style:

> Sarah Mendez is the lead engineer on Project Phoenix; you exchanged 12 emails between January and April 2026. Her most recent message was about milestone Beta — see [[Sarah Mendez]] in Google Data and [the original PDF brief](computer:///path/to/your/second-brain

Conventions:
- **Vault notes** → `[[Note Stem]]` wikilink. Use the full stem (filename without `.md`).
- **RAW files** → `computer:///path/to/your/second-brain with URL-encoded spaces. Build this from a `_Sources/` stub's `raw_path`.
- When listing many results, prefer a bulleted list with one citation per line:
  - `- 2024-03-07 — [[2024-03-07 Sabine acaba de enviarte un mensaje]] (LinkedIn DM)`

If you summarize a thread, link to the thread MOC (`[[<thread-slug>]]`) AND the most informative individual message.

## Common patterns

### "Who is X?" or "Find me X"

1. `Grep` for the name in `Vaults/Google Data/Contacts/` and `Vaults/ChatGPT/Concepts/`.
2. If a contact note exists, read it — it lists all exchanged emails by year.
3. Summarize: relationship, most recent contact date, count of interactions, top 2–3 conversations by relevance.

### "What did I discuss about <topic>?"

1. Check `_Topics/<topic>.md` and `_Communities/<topic>.md` MOCs first.
2. If no exact MOC match, `Grep` across both ChatGPT and Google Data for the topic keyword.
3. Group findings: ChatGPT conversations (often deep dives), email threads (often action items), DreamWorks/Zigurat notes (project-specific).

### "Find the document/contract/PDF/render for X"

1. `Grep` inside `_Sources/` directories across vaults — these have `filename:` frontmatter.
2. When you find the stub, read its `raw_path` and surface the `computer://` link directly to the user.
3. If they want the content, read the corresponding `Notes/<file>.md` in the same vault (already extracted) before reading RAW.

### "What's the latest on <project>?"

1. Read the project's `_Index.md` if it has its own vault, or the relevant `_Topics`/`_Communities` MOC.
2. Sort findings by date — frontmatter `date:`, `created:`, `updated:` fields are reliable.
3. Lead with the most recent item, work backwards.

### "Show me everything about <person/project>"

This is where the skill earns its keep. Cross-reference:
- All emails (Google Data Contacts/Threads)
- All ChatGPT conversations mentioning them (`Grep` ChatGPT vault)
- All vault notes referencing them (`Grep` everywhere)
- Any RAW source files attributed to them (look in source-stub `category` fields)

Group output by source vault, with date-sorted bullets per vault.

## Helper script

`scripts/sb_search.py` provides a fast cross-vault search with frontmatter awareness. Call it when grepping by hand would be tedious:

```bash
# Find any note mentioning a name, across all vaults
python3 sb_search.py "Sarah Mendez"

# Restrict to one vault
python3 sb_search.py --vault "Google Data" "Sabine"

# Resolve a _Sources/ stub to its raw_path (for piping into Read)
python3 sb_search.py --resolve-source "documento"

# Just list candidate vaults for a question
python3 sb_search.py --triage "what's my latest tax receipt"
```

The script returns markdown-formatted results with `[[wikilinks]]` already correct. Use it especially for "show me everything about X"-style questions where breadth matters.

## What to avoid

- **DO NOT write any files into the vault.** This skill is read-only. The answer goes back to the user as a chat reply, not as a saved markdown file. If you need scratch space, use `/tmp/` or the agent's outputs directory — never `/path/to/your/second-brain/Vaults/`. Writing into the vault is a serious mistake; it pollutes the user's curated knowledge graph with random output.
- **Don't dump entire vault notes into the answer.** Cite + brief excerpt is enough. The user can click through if they want more.
- **Don't search the `_Quarantine/` folder by default** — it's the bulk-mail dumping ground. Only search it when the user asks about marketing/automated content or a specific known sender that got quarantined.
- **Don't read RAW files speculatively.** If the vault Note has the answer, stop there.
- **Don't fabricate citations.** If you don't have a confident hit, say so and suggest where to look.

## Reference files

- `references/vault-layout.md` — the same conventions doc the curator skill uses (source-stub schema, marker blocks, color groups). Useful when answering "where is X stored" questions.
- `references/search-recipes.md` — concrete grep/glob commands for the most common question shapes.
