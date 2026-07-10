# Sample data

`sample_raw/` contains a tiny, entirely synthetic set of files so you can run
the curator end to end without any real data. Nothing here is personal.

```bash
python3 ../scripts/auto_curate_folder.py \
  --raw   ./sample_raw \
  --vaults /tmp/demo_vault \
  "Project Falcon"
```

Then open `/tmp/demo_vault` in Obsidian to see the generated `_Sources/`,
`Notes/`, `_Index.md`, and the linked graph.
