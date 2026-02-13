# Knowledge Graph Builder — Dev Log

## Goal

Build a knowledge graph from the git history of the `../codex` repository.
The graph captures relationships between **contributors**, **files**, and
**crates** through **commits** and **cargo metadata**, output as a single
`output/knowledge_graph.json`.

---

## Data model

```json
{
  "nodes": {
    "files":        [{ "id", "name", "previous_names", "file_type", "latest_line_count", "created_at", "last_modified_at", "deleted" }],
    "contributors": [{ "id", "name", "emails", "first_commit_at", "total_commits" }],
    "crates":       [{ "id", "name", "root_dir", "manifest_path", "edition", "has_lib", "has_bin" }]
  },
  "edges": [
    { "source (contributor)", "target (file)",  "type": "authored",   "commits" },
    { "source (crate)",       "target (crate)", "type": "depends_on" },
    { "source (crate)",       "target (file)",  "type": "contains" }
  ],
  "commits": { "<hash>": { "message", "author", "timestamp" } }
}
```

Authored edges are collapsed: one edge per (contributor, file) pair, with all
commit hashes listed. All edges carry a `type` field.

---

## Architecture

```
knowledge-graph/
├── build_graph.py            # Entry point — runs git log, orchestrates pipeline, writes JSON
├── git_log_parser.py         # Parses raw git log output into structured commit dicts
├── rename_tracker.py         # Assigns stable file IDs across renames/deletes/re-adds
├── graph_builder.py          # Assembles contributor nodes, collapsed edges, commit lookup
├── file_metadata.py          # Enriches file nodes with line counts and file types
├── rust-graph/
│   ├── crate_extractor.py    # Extracts crate nodes and edges from Cargo workspaces
│   ├── lsp_client.py         # Language server protocol client for rust-analyzer
│   └── tests/
│       ├── conftest.py
│       └── test_crate_extractor.py  (7 tests)
├── tests/
│   ├── conftest.py
│   ├── test_git_log_parser.py   (6 tests)
│   ├── test_rename_tracker.py   (8 tests)
│   ├── test_graph_builder.py    (7 tests)
│   ├── test_file_metadata.py    (7 tests)
│   └── test_integration.py      (2 tests)
├── output/
│   └── knowledge_graph.json  # Generated output
└── DEV_LOG.md                # This file
```

---

## Implementation summary

### Step 1 — `git_log_parser.py`

Splits raw `git log --name-status -M` output on `COMMIT_START\n` markers.
Parses each block into `{ hash, author, email, timestamp, message, changes }`.
Handles A/M/D/R statuses, quoted paths (spaces), and varying rename scores.

### Step 2 — `rename_tracker.py`

Maintains a live `path -> file_id` mapping, processing changes oldest-to-newest:
- **Add**: new file ID
- **Modify**: lookup existing ID (or create defensively)
- **Rename**: transfer ID from old path to new, append old path to `previous_names`
- **Delete**: mark deleted, remove from active mapping
- **Re-add after delete**: new file ID (git treats it as a new file)

### Step 3 — `graph_builder.py`

Iterates parsed commits, feeds changes to `RenameTracker`, builds contributor
nodes (keyed by author name, collecting multiple emails), accumulates edges as
`(contributor_id, file_id) -> [hashes]`, builds commit lookup, converts all
timestamps to ISO 8601, and calls `enrich_file_nodes` for metadata. All
contributor-file edges carry `"type": "authored"`.

### Step 4 — `file_metadata.py`

- `get_file_type`: extension extraction (handles hidden files like `.gitignore` → `None`)
- `get_line_count`: line count via Python file reading (`None` if missing)
- `enrich_file_nodes`: mutates file nodes in place, skips line count for deleted files

### Step 5 — `rust-graph/crate_extractor.py`

Extracts crate-level structure from Cargo workspaces using
`cargo metadata --no-deps --format-version 1`:

- `extract_crates`: parses workspace packages into crate nodes (sorted by name
  for deterministic IDs)
- `build_crate_dependency_edges`: filters dependencies to workspace-internal
  crates, produces `depends_on` edges
- `map_files_to_crates`: assigns each file to the crate whose `root_dir` is the
  longest prefix match, producing `contains` edges

### Step 6 — `build_graph.py`

Entry point: runs `git log` subprocess against `../codex`, pipes through the
parser and builder. Discovers Cargo workspaces (repo root + one level of
subdirectories), runs crate extraction for each, prefixes paths so crate
`root_dir` values are relative to the repo root. Writes output to
`output/knowledge_graph.json`.

### Step 7 — Tests

- **Unit tests** (30): git log parsing, rename tracking, graph building, file
  metadata, crate extraction (fixture-based, no cargo needed)
- **Integration tests** (2): temp git repo with 5 scripted commits; temp Cargo
  workspace with 2 crates validating the full pipeline including crate nodes,
  dependency edges, contains edges, edge types, and JSON round-trip

---

## Data extraction commands

```
git log --first-parent --reverse \
  --format="COMMIT_START%n%H%n%aN%n%aE%n%at%n%s" \
  --name-status -M
```

```
cargo metadata --no-deps --format-version 1
```

Git log is a single invocation parsed in Python. Cargo metadata is run per
workspace — `--no-deps` skips external dependencies.

---

## Test results

```
37 passed in 0.28s
```

All 37 unit + integration tests green.

---

## Output (codex repo)

| Metric | Value |
|---|---|
| Total commits | 3,632 |
| File nodes | 4,237 |
| Live files (at HEAD) | 2,628 |
| Deleted files | 1,609 |
| Renamed files | 179 |
| Contributors | 353 |
| Contributors with multiple emails | 7 |
| Crates | 66 |
| Authored edges | 10,674 |
| depends_on edges | 205 |
| contains edges | 2,873 |
| Total edges | 13,752 |

---

## Dependencies

- Python 3.10+
- `pytest` (tests only)
- `cargo` (crate extraction only — gracefully skipped for non-Rust repos)
- Standard library only (no third-party packages in main code)
