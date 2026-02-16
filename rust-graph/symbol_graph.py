"""Flatten symbol trees from JSONL into graph nodes and defined_in edges."""

import json


def extract_symbols(jsonl_path: str, path_prefix: str = "") -> list[dict]:
    """Read JSONL symbol trees and flatten into individual symbol nodes.

    Each JSONL line has {"file": "...", "root": {kind: "File", children: [...]}}.
    The File root is skipped (already represented as file nodes).  Children are
    DFS-flattened, sorted by (file, start_line, name), and assigned sequential
    IDs (symbol_1 .. symbol_N).  parent_symbol is set to the ID of the
    containing symbol, or None for top-level symbols.

    Args:
        jsonl_path: Path to the symbols JSONL file.
        path_prefix: String prepended to each file path (e.g. "codex-rs/").

    Returns:
        List of symbol node dicts.
    """
    # First pass: collect all flat symbols with a temporary parent index.
    flat: list[dict] = []

    with open(jsonl_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            file_path = path_prefix + entry["file"]
            root = entry["root"]
            # DFS over root.children (skip the File root itself)
            _flatten_children(root.get("children", []), file_path, flat, parent_idx=None)

    # Tag each symbol with its original index before sorting.
    for i, sym in enumerate(flat):
        sym["_orig_idx"] = i

    # Sort by (file, start_line, name) for deterministic IDs.
    flat.sort(key=lambda s: (s["file"], s["start_line"], s["name"]))

    # Assign sequential IDs and build orig_idx → new ID mapping.
    orig_to_id: dict[int, str] = {}
    for i, sym in enumerate(flat):
        sym_id = f"symbol_{i + 1}"
        sym["id"] = sym_id
        orig_to_id[sym["_orig_idx"]] = sym_id

    # Resolve parent_symbol references using the orig_idx mapping.
    for sym in flat:
        parent_idx = sym.pop("_parent_idx")
        sym.pop("_orig_idx")
        sym["parent_symbol"] = orig_to_id[parent_idx] if parent_idx is not None else None

    return flat


def _flatten_children(
    children: list[dict],
    file_path: str,
    out: list[dict],
    parent_idx: int | None,
) -> None:
    """DFS-flatten a list of symbol children into *out*."""
    for child in children:
        my_idx = len(out)
        out.append({
            "name": child["name"],
            "kind": child["kind"],
            "file": file_path,
            "start_line": child["start_line"],
            "end_line": child["end_line"],
            "line_count": child["line_count"],
            "detail": child.get("detail"),
            "signature": child.get("signature"),
            "_parent_idx": parent_idx,
        })
        _flatten_children(child.get("children", []), file_path, out, parent_idx=my_idx)


def build_defined_in_edges(symbol_nodes: list[dict], file_nodes: list[dict]) -> list[dict]:
    """Create one defined_in edge per symbol linking it to its file node.

    Symbols whose file path doesn't match any file node are silently skipped.

    Args:
        symbol_nodes: List of symbol dicts (each has "id" and "file").
        file_nodes: List of file node dicts (each has "id" and "name").

    Returns:
        List of edge dicts with type "defined_in".
    """
    file_lookup: dict[str, str] = {f["name"]: f["id"] for f in file_nodes}
    edges: list[dict] = []
    for sym in symbol_nodes:
        file_id = file_lookup.get(sym["file"])
        if file_id is not None:
            edges.append({
                "source": sym["id"],
                "target": file_id,
                "type": "defined_in",
            })
    return edges
