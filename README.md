# Knowledge Graph Builder

Build a knowledge graph from the git history of a repository. The graph captures
relationships between **contributors**, **files**, **crates**, **product
concepts**, and **code symbols** through git log analysis, Cargo metadata,
AI-generated summaries/tags, and rust-analyzer LSP.

Output: `output/knowledge_graph.json`

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

### Basic (git history only)

```bash
python build_graph.py
```

### Full pipeline (concepts, summaries, symbols)

```bash
python build_graph.py \
  --concept-yaml concept-graph/CODEX_CONCEPT_MAP.yaml \
  --summary-json summary-graph/output/summarize_file_20260214_222035.json \
  --tag-json summary-graph/output/tag_file_20260215_000309.json \
  --symbol-jsonl rust-graph/output/symbols_2026-02-16.jsonl
```

All flags are optional -- the pipeline works without them.

## Data model

Six node types and seven edge types:

| Node type | Example fields |
|---|---|
| **File** | path, rename history, line count, summary |
| **Contributor** | name, emails, commit count |
| **Crate** | name, root dir, edition, lib/bin targets |
| **Major Concept** | name, definition, evidence files |
| **Minor Concept** | name, definition, parent major concept |
| **Symbol** | name, kind, file, line range, signature |

| Edge type | Source | Target |
|---|---|---|
| `authored` | Contributor | File |
| `depends_on` | Crate | Crate |
| `contains` | Crate | File |
| `contributed_to` | Contributor | Crate |
| `has_minor` | Major Concept | Minor Concept |
| `tagged_with` | Concept | File |
| `defined_in` | Symbol | File |

See [DATA_MODEL.md](DATA_MODEL.md) for full schema and examples.

## Architecture

```
knowledge-graph/
├── build_graph.py            # Entry point
├── git_log_parser.py         # Parses git log --name-status -M
├── rename_tracker.py         # Stable file IDs across renames/deletes
├── graph_builder.py          # Assembles nodes, edges, commit lookup
├── file_metadata.py          # Line counts and file types
├── concept_extractor.py      # Concept nodes/edges from YAML + AI tags
├── concept-graph/            # Hand-curated concept map (YAML)
├── summary-graph/            # AI-powered file summarization (Anthropic API)
├── rust-graph/               # Crate extraction, symbol extraction (LSP)
├── webapp/                   # Interactive visualization (D3 force graph)
├── tests/                    # Unit and integration tests
└── output/                   # Generated knowledge_graph.json
```

## Visualization

An interactive canvas-based D3 force graph at `webapp/frontend/index.html`,
served by a zero-dependency Python HTTP server (`webapp/backend/server.py`).

Features: explore/overview modes, timeline filtering, visibility toggles,
contributor/file/crate/concept/symbol layers, live-reload on file changes.

```bash
python webapp/backend/server.py --port 8080
# Open http://localhost:8080
```

See [webapp/CLAUDE.md](webapp/CLAUDE.md) for dev server details.

## Tests

```bash
pytest                                    # 74 tests (unit + integration)
pytest summary-graph/test_summarize.py    # 30 tests (requires anthropic)
```

## Output stats (codex repo)

| Metric | Count |
|---|---|
| Commits | 3,632 |
| Files | 4,237 |
| Contributors | 353 |
| Crates | 66 |
| Concepts | 97 (9 major + 88 minor) |
| Symbols | ~25,470 |
| Total edges | ~42,435 |

## Dependencies

- Python 3.10+
- `pyyaml` (concept YAML parsing)
- `anthropic` (summary-graph only)
- `pytest` (tests only)
- `cargo` (crate extraction only)

## License

[BSD 3-Clause](LICENSE)
