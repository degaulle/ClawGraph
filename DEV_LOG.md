# Knowledge Graph Builder — Dev Log

## Goal

Build a knowledge graph from the git history of the `../codex` repository.
The graph captures relationships between **contributors** and **files** through
**commits**, output as a single `knowledge_graph.json`.

---

## Data model

```json
{
  "nodes": {
    "files":        [{ "id", "name", "previous_names", "file_type", "latest_line_count", "created_at", "last_modified_at", "deleted" }],
    "contributors": [{ "id", "name", "emails", "first_commit_at", "total_commits" }]
  },
  "edges":   [{ "source (contributor)", "target (file)", "commits" }],
  "commits": { "<hash>": { "message", "author", "timestamp" } }
}
```

Edges are collapsed: one edge per (contributor, file) pair, with all commit
hashes listed.

---

## Architecture

```
knowledge-graph/
├── build_graph.py            # Entry point — runs git log, orchestrates pipeline, writes JSON
├── git_log_parser.py         # Parses raw git log output into structured commit dicts
├── rename_tracker.py         # Assigns stable file IDs across renames/deletes/re-adds
├── graph_builder.py          # Assembles contributor nodes, collapsed edges, commit lookup
├── file_metadata.py          # Enriches file nodes with line counts and file types
├── tests/
│   ├── conftest.py
│   ├── test_git_log_parser.py   (6 tests)
│   ├── test_rename_tracker.py   (8 tests)
│   ├── test_graph_builder.py    (6 tests)
│   ├── test_file_metadata.py    (7 tests)
│   └── test_integration.py      (1 test — temp git repo with 5 scripted commits)
├── knowledge_graph.json      # Generated output
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
timestamps to ISO 8601, and calls `enrich_file_nodes` for metadata.

### Step 4 — `file_metadata.py`

- `get_file_type`: extension extraction (handles hidden files like `.gitignore` → `None`)
- `get_line_count`: line count via Python file reading (`None` if missing)
- `enrich_file_nodes`: mutates file nodes in place, skips line count for deleted files

### Step 5 — `build_graph.py`

Thin entry point: runs `git log` subprocess against `../codex`, pipes through
the parser and builder, writes `knowledge_graph.json`.

### Step 6 — Integration test

Creates a temporary git repo with 5 commits (2 authors, renames, deletes,
re-adds). Verifies 4 file nodes, 2 contributors, collapsed edges, commit
lookup, line counts, and JSON round-trip.

---

## Data extraction command

```
git log --first-parent --reverse \
  --format="COMMIT_START%n%H%n%aN%n%aE%n%at%n%s" \
  --name-status -M
```

Single invocation, parsed entirely in Python — no per-commit git calls.

---

## Test results

```
28 passed in 0.17s
```

All 28 unit + integration tests green.

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
| Edges | 10,674 |

---

## Dependencies

- Python 3.10+
- `pytest` (tests only)
- Standard library only (no third-party packages in main code)
