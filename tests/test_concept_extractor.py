"""Unit tests for concept_extractor using fixture data (no I/O to real YAML/JSON)."""

import json
import os
import tempfile

from concept_extractor import (
    extract_concepts,
    build_concept_hierarchy_edges,
    build_concept_file_edges,
    enrich_file_summaries,
)


def _write_yaml(content: str) -> str:
    """Write YAML content to a temp file, return its path."""
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path


def _write_json(data: dict) -> str:
    """Write JSON data to a temp file, return its path."""
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    return path


# --- Test 1: Extract concepts — sorting, IDs, fields, parent refs ---

def test_extract_concepts_basic():
    """Major concepts sort by name; minor concepts sort by (major_name, minor_name).
    IDs are assigned sequentially after sorting.  Each minor carries its parent's ID."""
    yaml = _write_yaml("""\
concepts:
  - major: Zulu
    definition: The last concept
    evidence:
      - z/file.rs
    minor:
      - concept: Zulu Minor B
        definition: Second minor under Zulu
        evidence:
          - z/b.rs
      - concept: Zulu Minor A
        definition: First minor under Zulu
        evidence:
          - z/a.rs
  - major: Alpha
    definition: The first concept
    evidence:
      - a/file.rs
    minor:
      - concept: Alpha Minor
        definition: Only minor under Alpha
        evidence:
          - a/m.rs
""")
    try:
        major, minor = extract_concepts(yaml)

        # Major sorted: Alpha (1), Zulu (2)
        assert len(major) == 2
        assert major[0]["id"] == "major_concept_1"
        assert major[0]["name"] == "Alpha"
        assert major[0]["definition"].startswith("The first")
        assert major[0]["evidence"] == ["a/file.rs"]
        assert major[1]["id"] == "major_concept_2"
        assert major[1]["name"] == "Zulu"

        # Minor sorted: (Alpha, Alpha Minor), (Zulu, Zulu Minor A), (Zulu, Zulu Minor B)
        assert len(minor) == 3
        assert minor[0]["id"] == "minor_concept_1"
        assert minor[0]["name"] == "Alpha Minor"
        assert minor[0]["major_concept"] == "major_concept_1"

        assert minor[1]["id"] == "minor_concept_2"
        assert minor[1]["name"] == "Zulu Minor A"
        assert minor[1]["major_concept"] == "major_concept_2"

        assert minor[2]["id"] == "minor_concept_3"
        assert minor[2]["name"] == "Zulu Minor B"
        assert minor[2]["major_concept"] == "major_concept_2"
    finally:
        os.unlink(yaml)


# --- Test 2: Hierarchy edges — one has_minor per minor concept ---

def test_build_concept_hierarchy_edges():
    major = [
        {"id": "major_concept_1", "name": "Alpha"},
        {"id": "major_concept_2", "name": "Beta"},
    ]
    minor = [
        {"id": "minor_concept_1", "name": "M1", "major_concept": "major_concept_1"},
        {"id": "minor_concept_2", "name": "M2", "major_concept": "major_concept_1"},
        {"id": "minor_concept_3", "name": "M3", "major_concept": "major_concept_2"},
    ]

    edges = build_concept_hierarchy_edges(major, minor)

    assert len(edges) == 3
    assert all(e["type"] == "has_minor" for e in edges)

    alpha_edges = [e for e in edges if e["source"] == "major_concept_1"]
    assert len(alpha_edges) == 2
    assert {e["target"] for e in alpha_edges} == {"minor_concept_1", "minor_concept_2"}

    beta_edges = [e for e in edges if e["source"] == "major_concept_2"]
    assert len(beta_edges) == 1
    assert beta_edges[0]["target"] == "minor_concept_3"


# --- Test 3: Concept→file edges from tag JSON (both major and minor tags) ---

def test_build_concept_file_edges():
    """Tags referencing major concepts produce major→file edges;
    tags referencing minor concepts produce minor→file edges.
    Unknown tags and unknown file paths are silently skipped."""
    tag_json = _write_json({
        "files": [
            {
                "source_path": "src/app.rs",
                "response": json.dumps({"tags": ["Conversation", "User Prompt"]}),
            },
            {
                "source_path": "src/session.rs",
                "response": json.dumps({"tags": ["Session Lifecycle"]}),
            },
            {
                # File not in file_nodes — should be skipped
                "source_path": "unknown/file.rs",
                "response": json.dumps({"tags": ["Conversation"]}),
            },
            {
                # Unknown tag — should be skipped
                "source_path": "src/app.rs",
                "response": json.dumps({"tags": ["Nonexistent Concept"]}),
            },
        ],
    })
    try:
        major = [
            {"id": "major_concept_1", "name": "Conversation"},
            {"id": "major_concept_2", "name": "Session Lifecycle"},
        ]
        minor = [
            {"id": "minor_concept_1", "name": "User Prompt", "major_concept": "major_concept_1"},
        ]
        file_nodes = [
            {"id": "file_1", "name": "src/app.rs"},
            {"id": "file_2", "name": "src/session.rs"},
        ]

        edges = build_concept_file_edges(tag_json, major, minor, file_nodes)

        assert all(e["type"] == "tagged_with" for e in edges)

        # src/app.rs tagged with Conversation (major) + User Prompt (minor) = 2 edges
        app_edges = [e for e in edges if e["target"] == "file_1"]
        assert len(app_edges) == 2
        assert {e["source"] for e in app_edges} == {"major_concept_1", "minor_concept_1"}

        # src/session.rs tagged with Session Lifecycle (major) = 1 edge
        session_edges = [e for e in edges if e["target"] == "file_2"]
        assert len(session_edges) == 1
        assert session_edges[0]["source"] == "major_concept_2"

        # Total: 3 edges (unknown file and unknown tag produce nothing)
        assert len(edges) == 3
    finally:
        os.unlink(tag_json)


# --- Test 4: Enrich file summaries — path matching, None for unmatched ---

def test_enrich_file_summaries():
    summary_json = _write_json({
        "files": [
            {"source_path": "src/app.rs", "response": "Main entry point."},
            {"source_path": "src/session.rs", "response": "Manages sessions."},
            {"source_path": "extra/orphan.rs", "response": "No matching file node."},
        ],
    })
    try:
        file_nodes = [
            {"id": "file_1", "name": "src/app.rs"},
            {"id": "file_2", "name": "src/session.rs"},
            {"id": "file_3", "name": "src/utils.rs"},  # no summary
        ]

        enrich_file_summaries(file_nodes, summary_json)

        assert file_nodes[0]["summary"] == "Main entry point."
        assert file_nodes[1]["summary"] == "Manages sessions."
        assert file_nodes[2]["summary"] is None
    finally:
        os.unlink(summary_json)


# --- Test 5: Stable IDs — same YAML produces identical results across runs ---

def test_stable_concept_ids():
    yaml = _write_yaml("""\
concepts:
  - major: Zulu
    definition: Last
    evidence: []
    minor: []
  - major: Alpha
    definition: First
    evidence: []
    minor:
      - concept: Sub
        definition: A sub
        evidence: []
""")
    try:
        run1_major, run1_minor = extract_concepts(yaml)
        run2_major, run2_minor = extract_concepts(yaml)

        assert run1_major == run2_major
        assert run1_minor == run2_minor

        # Alpha sorts before Zulu
        assert run1_major[0]["name"] == "Alpha"
        assert run1_major[0]["id"] == "major_concept_1"
    finally:
        os.unlink(yaml)


# --- Test 6: All generated edges carry a type field ---

def test_all_edges_have_type():
    major = [{"id": "major_concept_1", "name": "Test"}]
    minor = [{"id": "minor_concept_1", "name": "Sub", "major_concept": "major_concept_1"}]

    edges = build_concept_hierarchy_edges(major, minor)

    for edge in edges:
        assert "type" in edge
        assert "source" in edge
        assert "target" in edge
        assert edge["type"] == "has_minor"


# --- Test 7: Major concept with no minor children ---

def test_concepts_without_minor():
    yaml = _write_yaml("""\
concepts:
  - major: Solo
    definition: Has no minor concepts
    evidence:
      - solo.rs
""")
    try:
        major, minor = extract_concepts(yaml)

        assert len(major) == 1
        assert len(minor) == 0

        edges = build_concept_hierarchy_edges(major, minor)
        assert len(edges) == 0
    finally:
        os.unlink(yaml)


# --- Test 8: Duplicate tags on same file produce only one edge each ---

def test_duplicate_tags_deduplicated():
    """If the same file appears twice in the tag JSON with the same tag,
    only one tagged_with edge should be created per (concept, file) pair."""
    tag_json = _write_json({
        "files": [
            {
                "source_path": "src/app.rs",
                "response": json.dumps({"tags": ["Alpha", "Alpha"]}),
            },
        ],
    })
    try:
        major = [{"id": "major_concept_1", "name": "Alpha"}]
        file_nodes = [{"id": "file_1", "name": "src/app.rs"}]

        edges = build_concept_file_edges(tag_json, major, [], file_nodes)
        assert len(edges) == 1
    finally:
        os.unlink(tag_json)


# --- Test 9: Empty tag list produces no edges ---

def test_empty_tags():
    tag_json = _write_json({
        "files": [
            {"source_path": "src/app.rs", "response": json.dumps({"tags": []})},
        ],
    })
    try:
        major = [{"id": "major_concept_1", "name": "Alpha"}]
        file_nodes = [{"id": "file_1", "name": "src/app.rs"}]

        edges = build_concept_file_edges(tag_json, major, [], file_nodes)
        assert len(edges) == 0
    finally:
        os.unlink(tag_json)
