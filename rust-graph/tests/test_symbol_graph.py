"""Unit tests for symbol_graph (flatten JSONL trees into graph nodes)."""

import json
import os
import tempfile

from symbol_graph import extract_symbols, build_defined_in_edges


def _write_jsonl(entries: list[dict]) -> str:
    """Write entries to a temp JSONL file, return its path."""
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    return path


def _make_entry(file: str, children: list[dict]) -> dict:
    """Build a JSONL entry with a File root wrapping children."""
    total_lines = max((c["end_line"] for c in children), default=0)
    return {
        "file": file,
        "root": {
            "name": file,
            "kind": "File",
            "start_line": 1,
            "end_line": total_lines,
            "line_count": total_lines,
            "children": children,
        },
    }


def _make_child(name, kind, start_line, end_line, detail=None, signature=None,
                children=None):
    """Build a symbol child dict matching the JSONL format."""
    node = {
        "name": name,
        "kind": kind,
        "start_line": start_line,
        "end_line": end_line,
        "line_count": end_line - start_line + 1,
    }
    if detail is not None:
        node["detail"] = detail
    if signature is not None:
        node["signature"] = signature
    if children:
        node["children"] = children
    else:
        node["children"] = []
    return node


# --- Test 1: Basic extraction ---

def test_basic_extraction():
    """Correct count, fields, and top-level parent_symbol=None."""
    entries = [
        _make_entry("src/lib.rs", [
            _make_child("foo", "Function", 1, 10, detail="fn()"),
            _make_child("Bar", "Struct", 12, 20),
        ]),
    ]
    path = _write_jsonl(entries)
    try:
        symbols = extract_symbols(path)
        assert len(symbols) == 2

        # Sorted by (file, start_line, name) — foo at line 1, Bar at line 12
        assert symbols[0]["name"] == "foo"
        assert symbols[0]["id"] == "symbol_1"
        assert symbols[0]["kind"] == "Function"
        assert symbols[0]["file"] == "src/lib.rs"
        assert symbols[0]["start_line"] == 1
        assert symbols[0]["end_line"] == 10
        assert symbols[0]["line_count"] == 10
        assert symbols[0]["detail"] == "fn()"
        assert symbols[0]["parent_symbol"] is None

        assert symbols[1]["name"] == "Bar"
        assert symbols[1]["parent_symbol"] is None
    finally:
        os.unlink(path)


# --- Test 2: Nested symbols ---

def test_nested_symbols():
    """parent_symbol correctly references parent IDs."""
    entries = [
        _make_entry("src/lib.rs", [
            _make_child("MyStruct", "Struct", 1, 10, children=[
                _make_child("field_a", "Field", 2, 2, detail="i32"),
                _make_child("field_b", "Field", 3, 3, detail="String"),
            ]),
        ]),
    ]
    path = _write_jsonl(entries)
    try:
        symbols = extract_symbols(path)
        assert len(symbols) == 3

        struct = [s for s in symbols if s["name"] == "MyStruct"][0]
        field_a = [s for s in symbols if s["name"] == "field_a"][0]
        field_b = [s for s in symbols if s["name"] == "field_b"][0]

        assert struct["parent_symbol"] is None
        assert field_a["parent_symbol"] == struct["id"]
        assert field_b["parent_symbol"] == struct["id"]
    finally:
        os.unlink(path)


# --- Test 3: File root skipped ---

def test_file_root_skipped():
    """kind='File' root node should not appear in the output."""
    entries = [
        _make_entry("src/lib.rs", [
            _make_child("foo", "Function", 1, 5),
        ]),
    ]
    path = _write_jsonl(entries)
    try:
        symbols = extract_symbols(path)
        kinds = [s["kind"] for s in symbols]
        assert "File" not in kinds
        assert len(symbols) == 1
    finally:
        os.unlink(path)


# --- Test 4: Stable IDs ---

def test_stable_ids():
    """Two calls on the same input produce identical results."""
    entries = [
        _make_entry("src/lib.rs", [
            _make_child("bbb", "Function", 5, 10),
            _make_child("aaa", "Struct", 1, 3),
        ]),
    ]
    path = _write_jsonl(entries)
    try:
        run1 = extract_symbols(path)
        run2 = extract_symbols(path)
        assert run1 == run2
    finally:
        os.unlink(path)


# --- Test 5: Sort order ---

def test_sort_order():
    """File path dominates, then start_line, then name."""
    entries = [
        _make_entry("src/z.rs", [
            _make_child("alpha", "Function", 1, 5),
        ]),
        _make_entry("src/a.rs", [
            _make_child("beta", "Function", 10, 20),
            _make_child("alpha", "Function", 1, 5),
        ]),
    ]
    path = _write_jsonl(entries)
    try:
        symbols = extract_symbols(path)
        # src/a.rs comes before src/z.rs
        assert symbols[0]["file"] == "src/a.rs"
        assert symbols[0]["name"] == "alpha"  # line 1
        assert symbols[1]["file"] == "src/a.rs"
        assert symbols[1]["name"] == "beta"   # line 10
        assert symbols[2]["file"] == "src/z.rs"
    finally:
        os.unlink(path)


# --- Test 6: build_defined_in_edges basic ---

def test_build_defined_in_edges_basic():
    """One defined_in edge per symbol with a matching file."""
    symbol_nodes = [
        {"id": "symbol_1", "file": "src/lib.rs"},
        {"id": "symbol_2", "file": "src/lib.rs"},
        {"id": "symbol_3", "file": "src/main.rs"},
    ]
    file_nodes = [
        {"id": "file_1", "name": "src/lib.rs"},
        {"id": "file_2", "name": "src/main.rs"},
    ]
    edges = build_defined_in_edges(symbol_nodes, file_nodes)
    assert len(edges) == 3
    assert edges[0] == {"source": "symbol_1", "target": "file_1", "type": "defined_in"}
    assert edges[1] == {"source": "symbol_2", "target": "file_1", "type": "defined_in"}
    assert edges[2] == {"source": "symbol_3", "target": "file_2", "type": "defined_in"}


# --- Test 7: Missing file ---

def test_missing_file_no_edge():
    """Symbols whose file isn't in file_nodes produce no edge and no crash."""
    symbol_nodes = [
        {"id": "symbol_1", "file": "src/lib.rs"},
        {"id": "symbol_2", "file": "src/gone.rs"},
    ]
    file_nodes = [
        {"id": "file_1", "name": "src/lib.rs"},
    ]
    edges = build_defined_in_edges(symbol_nodes, file_nodes)
    assert len(edges) == 1
    assert edges[0]["source"] == "symbol_1"


# --- Test 8: All edges have type field ---

def test_edges_have_type_field():
    """Every edge dict has a 'type' key set to 'defined_in'."""
    symbol_nodes = [
        {"id": "symbol_1", "file": "a.rs"},
        {"id": "symbol_2", "file": "b.rs"},
    ]
    file_nodes = [
        {"id": "file_1", "name": "a.rs"},
        {"id": "file_2", "name": "b.rs"},
    ]
    edges = build_defined_in_edges(symbol_nodes, file_nodes)
    assert len(edges) > 0
    for edge in edges:
        assert "type" in edge
        assert edge["type"] == "defined_in"


# --- Test 9: path_prefix applied correctly ---

def test_path_prefix():
    """path_prefix is prepended to file paths in symbol nodes."""
    entries = [
        _make_entry("ansi-escape/src/lib.rs", [
            _make_child("expand_tabs", "Function", 6, 21, detail="fn()"),
        ]),
    ]
    path = _write_jsonl(entries)
    try:
        symbols = extract_symbols(path, path_prefix="codex-rs/")
        assert len(symbols) == 1
        assert symbols[0]["file"] == "codex-rs/ansi-escape/src/lib.rs"
    finally:
        os.unlink(path)
