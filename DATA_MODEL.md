# Knowledge Graph — Data Model

Output file: `output/knowledge_graph.json`

## Top-level structure

```json
{
  "nodes": {
    "files": [...],
    "contributors": [...],
    "crates": [...],
    "major_concepts": [...],
    "minor_concepts": [...],
    "symbols": [...]
  },
  "edges": [...],
  "commits": { "<hash>": {...}, ... }
}
```

## Node types

### File

```json
{
  "id": "file_1",
  "name": ".github/workflows/ci.yml",
  "previous_names": [],
  "deleted": false,
  "created_at": "2025-04-16T16:56:08+00:00",
  "last_modified_at": "2025-12-18T19:53:36+00:00",
  "file_type": "yml",
  "latest_line_count": 66,
  "summary": "CI workflow configuration for GitHub Actions."
}
```

| Field | Type | Notes |
|---|---|---|
| `id` | `"file_N"` | Stable across renames |
| `name` | `string` | Path relative to repo root |
| `previous_names` | `string[]` | Rename history (oldest first) |
| `deleted` | `bool` | `true` if file no longer exists at HEAD |
| `created_at` | `ISO 8601` | Timestamp of first commit touching this file |
| `last_modified_at` | `ISO 8601` | Timestamp of most recent commit |
| `file_type` | `string \| null` | Extension (e.g. `"rs"`, `"yml"`); `null` for dotfiles |
| `latest_line_count` | `int \| null` | Line count at HEAD; `null` if deleted |
| `summary` | `string \| null` | AI-generated summary of the file's purpose; `null` if not available |

### Contributor

```json
{
  "id": "contributor_1",
  "name": "Alice",
  "emails": ["alice@example.com", "alice@work.com"],
  "first_commit_at": "2025-04-16T16:56:08+00:00",
  "total_commits": 42
}
```

| Field | Type | Notes |
|---|---|---|
| `id` | `"contributor_N"` | Assigned in commit-order |
| `name` | `string` | Git author name |
| `emails` | `string[]` | All emails seen for this author |
| `first_commit_at` | `ISO 8601` | Earliest commit timestamp |
| `total_commits` | `int` | Number of commits by this contributor |

### Crate

```json
{
  "id": "crate_2",
  "name": "codex-ansi-escape",
  "root_dir": "codex-rs/ansi-escape/",
  "manifest_path": "codex-rs/ansi-escape/Cargo.toml",
  "edition": "2024",
  "has_lib": true,
  "has_bin": false,
  "created_at": "2025-05-01T10:00:00+00:00"
}
```

| Field | Type | Notes |
|---|---|---|
| `id` | `"crate_N"` | Deterministic — sorted by crate name |
| `name` | `string` | Cargo package name |
| `root_dir` | `string` | Directory containing `Cargo.toml`, relative to repo root, trailing `/` |
| `manifest_path` | `string` | Path to `Cargo.toml`, relative to repo root |
| `edition` | `string` | Rust edition (e.g. `"2021"`, `"2024"`) |
| `has_lib` | `bool` | Has a `lib` or `proc-macro` target |
| `has_bin` | `bool` | Has a `bin` target |
| `created_at` | `ISO 8601 \| null` | Timestamp of first commit adding the crate's `Cargo.toml`; `null` if not in git history |

### Major Concept

```json
{
  "id": "major_concept_1",
  "name": "Conversation",
  "definition": "The primary interaction paradigm: a streaming, turn-based chat...",
  "evidence": ["codex-rs/tui/src/chatwidget.rs", "codex-rs/core/src/context_manager.rs"]
}
```

| Field | Type | Notes |
|---|---|---|
| `id` | `"major_concept_N"` | Deterministic — sorted by concept name |
| `name` | `string` | Product/feature concept name |
| `definition` | `string` | 1–2 sentence description |
| `evidence` | `string[]` | File paths that exemplify this concept |

### Minor Concept

```json
{
  "id": "minor_concept_1",
  "name": "User Prompt",
  "definition": "A natural-language message the user types into the composer...",
  "evidence": ["codex-rs/tui/src/bottom_pane/chat_composer.rs"],
  "major_concept": "major_concept_1"
}
```

| Field | Type | Notes |
|---|---|---|
| `id` | `"minor_concept_N"` | Deterministic — sorted by (major name, minor name) |
| `name` | `string` | Product/feature concept name |
| `definition` | `string` | 1–2 sentence description |
| `evidence` | `string[]` | File paths that exemplify this concept |
| `major_concept` | `"major_concept_N"` | ID of the parent major concept |

### Symbol

```json
{
  "id": "symbol_1",
  "name": "expand_tabs",
  "kind": "Function",
  "file": "codex-rs/ansi-escape/src/lib.rs",
  "start_line": 6,
  "end_line": 21,
  "line_count": 16,
  "detail": "fn(s: &str) -> Cow<'_, str>",
  "signature": null,
  "parent_symbol": null
}
```

| Field | Type | Notes |
|---|---|---|
| `id` | `"symbol_N"` | Deterministic — sorted by (file, start_line, name) |
| `name` | `string` | Symbol name (function, struct, field, etc.) |
| `kind` | `string` | Symbol kind (e.g. `"Function"`, `"Struct"`, `"Field"`, `"Module"`) |
| `file` | `string` | File path relative to repo root |
| `start_line` | `int` | First line of the symbol (1-indexed) |
| `end_line` | `int` | Last line of the symbol (1-indexed) |
| `line_count` | `int` | Number of lines the symbol spans |
| `detail` | `string \| null` | Type signature or detail string from LSP; `null` if unavailable |
| `signature` | `string \| null` | Full function/method signature from hover; `null` for non-function kinds |
| `parent_symbol` | `"symbol_N" \| null` | ID of containing symbol (e.g. a Field's parent Struct); `null` for top-level |

## Edge types

All edges have a `type` field. Seven types exist:

### `authored` — contributor wrote/modified a file

```json
{
  "source": "contributor_1",
  "target": "file_1",
  "type": "authored",
  "commits": ["59a180dd..."]
}
```

Collapsed: one edge per (contributor, file) pair. `commits` lists all commit
hashes where this contributor touched this file.

### `depends_on` — crate depends on another workspace crate

```json
{
  "source": "crate_10",
  "target": "crate_11",
  "type": "depends_on"
}
```

Only workspace-internal dependencies. No external crates, no self-edges.

### `contains` — crate owns a file

```json
{
  "source": "crate_2",
  "target": "file_237",
  "type": "contains"
}
```

Assigned by longest-prefix match of file path against crate `root_dir`.
Files outside any crate (e.g. repo-root `Cargo.lock`) have no `contains` edge.

### `contributed_to` — contributor contributed to a crate

```json
{
  "source": "contributor_1",
  "target": "crate_2",
  "type": "contributed_to",
  "total_commits": 15,
  "first_contribution_at": "2025-05-03T12:34:56+00:00"
}
```

Derived by joining `authored` (contributor→file) and `contains` (crate→file)
edges. One edge per (contributor, crate) pair. `total_commits` is the number of
distinct commits where the contributor touched files in the crate.
`first_contribution_at` is the timestamp of the earliest such commit.

### `has_minor` — major concept contains a minor concept

```json
{
  "source": "major_concept_1",
  "target": "minor_concept_3",
  "type": "has_minor"
}
```

One edge per minor concept, linking it to its parent major concept.

### `tagged_with` — concept is associated with a file

```json
{
  "source": "minor_concept_3",
  "target": "file_42",
  "type": "tagged_with"
}
```

Derived from AI-generated tags. Both major and minor concepts can tag files.
One edge per (concept, file) pair. Deduplicated.

### `defined_in` — symbol is defined in a file

```json
{
  "source": "symbol_42",
  "target": "file_239",
  "type": "defined_in"
}
```

One edge per symbol, linking it to the file where it is defined.
Symbols whose file path doesn't match any file node are silently skipped.

## Commits

```json
{
  "59a180dd...": {
    "message": "Initial commit",
    "author": "contributor_1",
    "timestamp": "2025-04-16T16:56:08+00:00"
  }
}
```

Keyed by full SHA. `author` references a contributor ID.

## Current stats (codex repo)

| Metric | Count |
|---|---|
| Files | 4,237 |
| Files with summaries | 888 |
| Contributors | 353 |
| Crates | 66 |
| Major concepts | 9 |
| Minor concepts | 88 |
| Symbols | ~25,470 |
| Commits | 3,632 |
| `authored` edges | 10,674 |
| `depends_on` edges | 205 |
| `contains` edges | 2,873 |
| `contributed_to` edges | 958 |
| `has_minor` edges | 88 |
| `tagged_with` edges | 2,167 |
| `defined_in` edges | ~25,470 |
| Total edges | ~42,435 |
