# ClawGraph — Dev Log

## Goal

Build a knowledge graph from the git history of the `../codex` repository.
The graph captures relationships between **contributors**, **files**,
**crates**, and **product concepts** through **commits**, **cargo metadata**,
and **AI-generated summaries/tags**, output as a single
`output/knowledge_graph.json`.

---

## Data model

```json
{
  "nodes": {
    "files":          [{ "id", "name", "previous_names", "file_type", "latest_line_count", "created_at", "last_modified_at", "deleted", "summary" }],
    "contributors":   [{ "id", "name", "emails", "first_commit_at", "total_commits" }],
    "crates":         [{ "id", "name", "root_dir", "manifest_path", "edition", "has_lib", "has_bin", "created_at" }],
    "major_concepts": [{ "id", "name", "definition", "evidence" }],
    "minor_concepts": [{ "id", "name", "definition", "evidence", "major_concept" }],
    "symbols":        [{ "id", "name", "kind", "file", "start_line", "end_line", "line_count", "detail", "signature", "parent_symbol" }]
  },
  "edges": [
    { "source (contributor)",    "target (file)",          "type": "authored",        "commits" },
    { "source (crate)",          "target (crate)",         "type": "depends_on" },
    { "source (crate)",          "target (file)",          "type": "contains" },
    { "source (contributor)",    "target (crate)",         "type": "contributed_to",  "total_commits", "first_contribution_at" },
    { "source (major_concept)",  "target (minor_concept)", "type": "has_minor" },
    { "source (concept)",        "target (file)",          "type": "tagged_with" },
    { "source (symbol)",         "target (file)",          "type": "defined_in" }
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
├── concept_extractor.py      # Extracts concept nodes/edges from YAML; enriches files with summaries
├── concept-graph/
│   ├── CODEX_CONCEPT_MAP.yaml              # Full concept map with definitions and evidence
│   ├── CODEX_CONCEPT_MAP_DEFINITIONS_ONLY.yaml
│   └── CODEX_CONCEPT_MAP.prompt
├── summary-graph/
│   ├── summarize.py          # Single-file summarizer using Anthropic API
│   ├── batch_summarize.py    # Batch summarizer with thread pool and resume support
│   ├── test_summarize.py     # Tests for summarize/batch_summarize (30 tests)
│   ├── template/             # Prompt templates and JSON schemas
│   └── output/               # Generated summaries and tags
├── rust-graph/
│   ├── crate_extractor.py    # Extracts crate nodes and edges from Cargo workspaces
│   ├── symbol_extractor.py   # Extracts symbol trees from Rust files via rust-analyzer LSP
│   ├── symbol_graph.py       # Flattens symbol trees into graph nodes and defined_in edges
│   ├── lsp_client.py         # Language server protocol client for rust-analyzer
│   └── tests/
│       ├── conftest.py
│       ├── test_crate_extractor.py   (10 tests)
│       ├── test_symbol_extractor.py  (16 tests)
│       └── test_symbol_graph.py      (9 tests)
├── tests/
│   ├── conftest.py
│   ├── test_git_log_parser.py      (6 tests)
│   ├── test_rename_tracker.py      (8 tests)
│   ├── test_graph_builder.py       (7 tests)
│   ├── test_file_metadata.py       (7 tests)
│   ├── test_concept_extractor.py   (9 tests)
│   └── test_integration.py         (2 tests)
├── requirements.txt          # Python dependencies
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
- `enrich_crate_created_at`: sets each crate's `created_at` from the
  `created_at` of its `Cargo.toml` file node
- `build_crate_dependency_edges`: filters dependencies to workspace-internal
  crates, produces `depends_on` edges
- `map_files_to_crates`: assigns each file to the crate whose `root_dir` is the
  longest prefix match, producing `contains` edges
- `build_contributor_crate_edges`: derives `contributed_to` edges by joining
  `authored` (contributor→file) and `contains` (crate→file) edges; one edge per
  (contributor, crate) pair with `total_commits` and `first_contribution_at`

### Step 6 — `concept_extractor.py`

Extracts product/feature concepts from a hand-curated YAML concept map and
AI-generated JSON files:

- `extract_concepts`: parses YAML into major and minor concept nodes (sorted
  by name for deterministic IDs)
- `build_concept_hierarchy_edges`: produces `has_minor` edges from the
  major→minor hierarchy
- `build_concept_file_edges`: parses tag JSON (AI-generated concept tags per
  file), matches tag names to concept IDs and `source_path` to file IDs,
  produces deduplicated `tagged_with` edges for both major and minor concepts
- `enrich_file_summaries`: mutates file nodes in place, adding a `summary`
  field from the summary JSON (matched by `source_path` to file `name`)

### Step 7 — `rust-graph/symbol_graph.py`

Flattens the hierarchical symbol trees produced by `symbol_extractor.py` (JSONL)
into individual graph nodes and `defined_in` edges:

- `extract_symbols`: reads JSONL, DFS-flattens each file's `root.children`
  (skipping the File root), prepends `path_prefix` to file paths, sorts by
  `(file, start_line, name)` for deterministic IDs, and resolves `parent_symbol`
  references so nested symbols (e.g. fields inside structs) point to their
  containing symbol's ID
- `build_defined_in_edges`: builds `file["name"] -> file["id"]` lookup, emits
  one `defined_in` edge per symbol with a matching file node

### Step 8 — `build_graph.py`

Entry point: runs `git log` subprocess against `../codex`, pipes through the
parser and builder. Accepts optional CLI arguments `--concept-yaml`,
`--summary-json`, `--tag-json`, `--symbol-jsonl`, and `--symbol-prefix` for
concept, summary, and symbol enrichment. Discovers Cargo workspaces (repo root
+ one level of subdirectories), runs crate extraction for each, prefixes paths
so crate `root_dir` values are relative to the repo root. Writes output to
`output/knowledge_graph.json`.

### Step 9 — Tests

- **Unit tests** (49): git log parsing, rename tracking, graph building, file
  metadata, crate extraction, concept extraction, symbol graph flattening
  (fixture-based, no external tools needed)
- **Symbol extractor tests** (16): symbol conversion, hover/signature, file
  tree building
- **Summary-graph tests** (30): prompt building, file discovery, API mocking,
  rate-limit retry, resume logic
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

### Full pipeline with concepts, summaries, and symbols

```
python build_graph.py \
  --concept-yaml concept-graph/CODEX_CONCEPT_MAP.yaml \
  --summary-json summary-graph/output/summarize_file_20260214_222035.json \
  --tag-json summary-graph/output/tag_file_20260215_000309.json \
  --symbol-jsonl rust-graph/output/symbols_2026-02-16.jsonl
```

All flags are optional — the pipeline works without them for
backward-compatible output.

---

## Test results

```
74 passed in 0.37s
```

All 74 tests green (58 unit/integration + 16 symbol extractor).
Summary-graph tests (30) require the `anthropic` module and run separately.

---

## Output (codex repo)

| Metric | Value |
|---|---|
| Total commits | 3,632 |
| File nodes | 4,237 |
| Files with summaries | 888 |
| Live files (at HEAD) | 2,628 |
| Deleted files | 1,609 |
| Renamed files | 179 |
| Contributors | 353 |
| Contributors with multiple emails | 7 |
| Crates | 66 |
| Major concepts | 9 |
| Minor concepts | 88 |
| Symbols | ~25,470 |
| Authored edges | 10,674 |
| depends_on edges | 205 |
| contains edges | 2,873 |
| contributed_to edges | 958 |
| has_minor edges | 88 |
| tagged_with edges | 2,167 |
| defined_in edges | ~25,470 |
| Total edges | ~42,435 |

---

## Dependencies

- Python 3.10+
- `pyyaml` (concept YAML parsing)
- `anthropic` (summary-graph only — AI-powered file summarization)
- `pytest` (tests only)
- `cargo` (crate extraction only — gracefully skipped for non-Rust repos)

All dependencies listed in `requirements.txt`. Install via:
```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```
