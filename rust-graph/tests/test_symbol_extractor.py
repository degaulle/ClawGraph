"""Unit tests for symbol_extractor using fixture data (no LSP needed)."""

from symbol_extractor import (
    _convert_symbol,
    _build_file_tree,
    HOVER_KINDS,
)
from lsp_client import SYMBOL_KINDS


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_symbol(name, kind, start_line, end_line, detail=None, children=None,
                 start_col=0, end_col=0, sel_start_line=None, sel_start_col=None):
    """Build a minimal DocumentSymbol dict (0-indexed lines, as from LSP)."""
    sym = {
        "name": name,
        "kind": kind,
        "range": {
            "start": {"line": start_line, "character": start_col},
            "end": {"line": end_line, "character": end_col},
        },
        "selectionRange": {
            "start": {
                "line": sel_start_line if sel_start_line is not None else start_line,
                "character": sel_start_col if sel_start_col is not None else start_col,
            },
            "end": {"line": start_line, "character": end_col},
        },
    }
    if detail is not None:
        sym["detail"] = detail
    if children:
        sym["children"] = children
    return sym


# ---------------------------------------------------------------------------
# _convert_symbol tests
# ---------------------------------------------------------------------------

def test_convert_basic_struct():
    """A struct symbol converts with correct fields and 1-indexed lines."""
    sym = _make_symbol("ChatComposer", kind=23, start_line=264, end_line=311)
    node = _convert_symbol(sym, hover_fn=None)

    assert node["name"] == "ChatComposer"
    assert node["kind"] == "Struct"
    assert node["start_line"] == 265  # 0-indexed 264 → 1-indexed 265
    assert node["end_line"] == 312    # 0-indexed 311 → 1-indexed 312
    assert node["line_count"] == 48
    assert node["children"] == []
    assert node["detail"] is None


def test_convert_preserves_detail():
    """The detail field from DocumentSymbol is passed through."""
    sym = _make_symbol("textarea", kind=8, start_line=266, end_line=266,
                       detail="TextArea<'static>")
    node = _convert_symbol(sym, hover_fn=None)

    assert node["detail"] == "TextArea<'static>"


def test_convert_nested_children():
    """Children are recursively converted."""
    child1 = _make_symbol("text", kind=8, start_line=193, end_line=193,
                          detail="String")
    child2 = _make_symbol("text_elements", kind=8, start_line=194, end_line=194,
                          detail="Vec<TextElement>")
    parent = _make_symbol("Submitted", kind=22, start_line=192, end_line=195,
                          children=[child1, child2])
    node = _convert_symbol(parent, hover_fn=None)

    assert len(node["children"]) == 2
    assert node["children"][0]["name"] == "text"
    assert node["children"][0]["detail"] == "String"
    assert node["children"][1]["name"] == "text_elements"


def test_convert_single_line_symbol():
    """A symbol spanning one line has line_count = 1."""
    sym = _make_symbol("THRESHOLD", kind=14, start_line=10, end_line=10)
    node = _convert_symbol(sym, hover_fn=None)

    assert node["line_count"] == 1
    assert node["start_line"] == 11
    assert node["end_line"] == 11


def test_kind_mapping_covers_common_rust_kinds():
    """All common Rust symbol kinds map to human-readable strings."""
    expected = {
        2: "Module", 6: "Method", 8: "Field", 10: "Enum",
        12: "Function", 14: "Constant", 22: "EnumMember", 23: "Struct",
        19: "Object",
    }
    for kind_num, kind_str in expected.items():
        assert SYMBOL_KINDS.get(kind_num) == kind_str


# ---------------------------------------------------------------------------
# Hover / signature tests
# ---------------------------------------------------------------------------

def test_signature_populated_for_function():
    """Functions (kind 12) get a signature from hover."""
    sym = _make_symbol("run_main", kind=12, start_line=88, end_line=150,
                       sel_start_line=88, sel_start_col=13)

    def mock_hover(relpath, line, col):
        assert line == 88     # selectionRange.start.line (0-indexed)
        assert col == 13      # selectionRange.start.character
        return {"contents": {"value": "pub async fn run_main(cli: Cli) -> Result<()>"}}

    node = _convert_symbol(sym, hover_fn=mock_hover)
    assert node["signature"] == "pub async fn run_main(cli: Cli) -> Result<()>"


def test_signature_populated_for_method():
    """Methods (kind 6) also get a signature from hover."""
    sym = _make_symbol("handle_paste", kind=6, start_line=557, end_line=617,
                       sel_start_line=557, sel_start_col=11)

    def mock_hover(relpath, line, col):
        return {"contents": {"value": "pub fn handle_paste(&mut self, text: &str)"}}

    node = _convert_symbol(sym, hover_fn=mock_hover)
    assert node["signature"] == "pub fn handle_paste(&mut self, text: &str)"


def test_no_signature_for_struct():
    """Non-function kinds (e.g. Struct, kind 23) do not get hover calls."""
    hover_called = False

    def mock_hover(relpath, line, col):
        nonlocal hover_called
        hover_called = True
        return None

    sym = _make_symbol("ChatComposer", kind=23, start_line=264, end_line=311)
    node = _convert_symbol(sym, hover_fn=mock_hover)

    assert not hover_called
    assert "signature" not in node


def test_no_signature_for_field():
    """Fields (kind 8) do not get hover calls."""
    hover_called = False

    def mock_hover(relpath, line, col):
        nonlocal hover_called
        hover_called = True

    sym = _make_symbol("name", kind=8, start_line=10, end_line=10, detail="String")
    node = _convert_symbol(sym, hover_fn=mock_hover)

    assert not hover_called
    assert "signature" not in node


def test_signature_null_when_hover_returns_none():
    """If hover returns None, signature should be None (not crash)."""
    sym = _make_symbol("broken_fn", kind=12, start_line=5, end_line=10)

    def mock_hover(relpath, line, col):
        return None

    node = _convert_symbol(sym, hover_fn=mock_hover)
    assert node["signature"] is None


def test_signature_handles_string_contents():
    """Some LSP servers return contents as a plain string instead of {value:...}."""
    sym = _make_symbol("simple_fn", kind=12, start_line=5, end_line=10)

    def mock_hover(relpath, line, col):
        return {"contents": "fn simple_fn() -> bool"}

    node = _convert_symbol(sym, hover_fn=mock_hover)
    assert node["signature"] == "fn simple_fn() -> bool"


def test_hover_kinds_are_function_and_method():
    """Only Function (12) and Method (6) should trigger hover."""
    assert 6 in HOVER_KINDS
    assert 12 in HOVER_KINDS
    assert len(HOVER_KINDS) == 2


# ---------------------------------------------------------------------------
# _build_file_tree tests
# ---------------------------------------------------------------------------

def test_build_file_tree_wraps_in_root():
    """Top-level symbols are wrapped in a File root node."""
    symbols = [
        _make_symbol("Foo", kind=23, start_line=0, end_line=5),
        _make_symbol("bar", kind=12, start_line=7, end_line=15),
    ]
    tree = _build_file_tree("src/lib.rs", symbols, total_lines=16, hover_fn=None)

    assert tree["name"] == "src/lib.rs"
    assert tree["kind"] == "File"
    assert tree["start_line"] == 1
    assert tree["end_line"] == 16
    assert tree["line_count"] == 16
    assert len(tree["children"]) == 2
    assert tree["children"][0]["name"] == "Foo"
    assert tree["children"][1]["name"] == "bar"


def test_build_file_tree_empty_file():
    """A file with no symbols produces a root with empty children."""
    tree = _build_file_tree("empty.rs", [], total_lines=0, hover_fn=None)

    assert tree["kind"] == "File"
    assert tree["children"] == []
    assert tree["line_count"] == 0


def test_build_file_tree_preserves_hierarchy():
    """Nested DocumentSymbols produce a nested tree."""
    field = _make_symbol("x", kind=8, start_line=2, end_line=2, detail="i32")
    struct = _make_symbol("Point", kind=23, start_line=1, end_line=3,
                          children=[field])
    tree = _build_file_tree("point.rs", [struct], total_lines=4, hover_fn=None)

    assert len(tree["children"]) == 1
    point = tree["children"][0]
    assert point["name"] == "Point"
    assert len(point["children"]) == 1
    assert point["children"][0]["name"] == "x"
    assert point["children"][0]["detail"] == "i32"


def test_build_file_tree_hover_passed_to_functions():
    """Hover is called for functions inside the tree."""
    fn_sym = _make_symbol("do_thing", kind=12, start_line=5, end_line=20,
                          sel_start_line=5, sel_start_col=7)
    struct_sym = _make_symbol("MyStruct", kind=23, start_line=0, end_line=3)

    hover_calls = []

    def mock_hover(relpath, line, col):
        hover_calls.append((relpath, line, col))
        return {"contents": {"value": "fn do_thing()"}}

    tree = _build_file_tree("lib.rs", [struct_sym, fn_sym], total_lines=21,
                            hover_fn=mock_hover)

    # Hover should be called exactly once (for the function, not the struct)
    assert len(hover_calls) == 1
    fn_node = tree["children"][1]
    assert fn_node["signature"] == "fn do_thing()"
