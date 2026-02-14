# Knowledge Graph — Data Model

Output file: `output/knowledge_graph.json`

## Top-level structure

```json
{
  "nodes": { "files": [...], "contributors": [...], "crates": [...] },
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
  "latest_line_count": 66
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

## Edge types

All edges have a `type` field. Three types exist:

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
| Contributors | 353 |
| Crates | 66 |
| Commits | 3,632 |
| `authored` edges | 10,674 |
| `depends_on` edges | 205 |
| `contains` edges | 2,873 |
| Total edges | 13,752 |
