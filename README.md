# ClawGraph

**An interactive visual interface for human-agent co-coding, built on knowledge graphs.**

---

## Why ClawGraph?

It's 2026. Agentic systems like Claude Code can ship features, refactor systems, and open PRs -- sometimes faster than whole teams. But the way we steer all that power? Still a chat box. Still a scroll. Still text.

The moment your work gets real -- complex projects, long timelines, multiple stakeholders -- chat breaks down. **Context rot**: important constraints fall out of the window, threads get compacted, key details get summarized away. One week later, you can't remember which assumptions were real and which were just implied. Then comes **organizational memory loss**: the "why" lives in buried logs until it disappears into scrollback. Your team inherits answers without the reasoning.

But humans don't think in straight lines. Projects branch like a tree -- decisions, dependencies, alternatives, timelines. So why are we controlling the strongest agents we've ever built through a linear log?

ClawGraph gives agents the interface that matches how we actually think: **Understand, Explore, Create.**

### Three innovations

1. **Opus 4.6-powered knowledge indexing** -- Automatically constructs a knowledge graph of any codebase repository, extracting contributors, files, crates, product concepts, and code symbols.
2. **Fully interactive visual interface** -- Humans explore and understand the knowledge graph through a canvas-based force graph with drill-down navigation, timeline filtering, and concept layering.
3. **Deep integration with existing agents and tools** -- ClawGraph implements its own MCP server, so Claude Code sees exactly what you see. Click a symbol and jump straight into Cursor at the exact line. Understand a codebase in minutes, not weeks.

### Demo: OpenAI Codex

To demonstrate the system in a real-world scenario, we ran ClawGraph against the entire codebase of OpenAI's coding agent, **Codex** -- thousands of files and hundreds of thousands of lines of code that used to take weeks to fully understand, reduced to minutes.

The home page shows Codex as the root node with nine major concept nodes that Opus 4.6 automatically summarized during the indexing phase. Click a major concept to expand its minor concepts and tagged files. Drill into a specific minor concept to see connected files. Select a file to see every contributor who worked on it, read an AI-generated summary, browse its symbols, and click a line number to open Cursor at that exact definition. Switch to overview mode to see every node and edge at once, then use the timeline filter to watch how the codebase was built step by step.

---

## What it does

Builds a knowledge graph from the git history of a repository, capturing
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
