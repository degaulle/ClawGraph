#!/usr/bin/env python3
"""
Extract the full symbol hierarchy of Rust source files via rust-analyzer's LSP.

Produces one JSONL record per file (streaming): each line is a self-contained
JSON object with the file's symbol tree. Function/Method nodes include their
full type signature from hover unless --no-signatures is passed.

Usage:
    python3 symbol_extractor.py <file1> [file2 ...]
    python3 symbol_extractor.py --all-rs                # workspace .rs files only
    python3 symbol_extractor.py --all-rs --no-signatures # skip hover (much faster)

Output: JSONL to stdout (one JSON object per line). Progress to stderr.
"""

import json
import os
import subprocess
import sys

from lsp_client import LspClient, SYMBOL_KINDS, PROJECT_ROOT, RA_SINGLE_CRATE

# Kinds that get a hover-based signature.
HOVER_KINDS = {6, 12}  # Method, Function


def _parse_hover_signature(hover_result) -> str | None:
    """Extract a clean signature string from an LSP hover response."""
    if not hover_result:
        return None
    contents = hover_result.get("contents", {})
    if isinstance(contents, dict):
        value = contents.get("value", "")
    elif isinstance(contents, str):
        value = contents
    else:
        return None
    if not value:
        return None
    # rust-analyzer returns markdown fenced code blocks; extract the code.
    lines = value.strip().splitlines()
    # Strip ```rust / ``` fences if present.
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    sig = "\n".join(lines).strip()
    return sig or None


def _convert_symbol(sym: dict, hover_fn=None, relpath=None) -> dict:
    """Recursively convert an LSP DocumentSymbol into our node format.

    hover_fn: optional callable(relpath, line, col) -> hover result dict.
              Only called for Function/Method kinds.
    relpath:  file relative path, passed through to hover_fn.
    """
    kind_id = sym.get("kind", 0)
    kind_str = SYMBOL_KINDS.get(kind_id, f"Unknown({kind_id})")

    rng = sym.get("range", {})
    start_line = rng.get("start", {}).get("line", 0) + 1  # 0→1 indexed
    end_line = rng.get("end", {}).get("line", 0) + 1
    line_count = end_line - start_line + 1

    node: dict = {
        "name": sym.get("name", ""),
        "kind": kind_str,
        "detail": sym.get("detail") or None,
        "start_line": start_line,
        "end_line": end_line,
        "line_count": line_count,
    }

    # Fetch signature for functions/methods.
    if kind_id in HOVER_KINDS:
        sel = sym.get("selectionRange", rng)
        sel_start = sel.get("start", {})
        hover_line = sel_start.get("line", 0)
        hover_col = sel_start.get("character", 0)
        try:
            hover = hover_fn(relpath, hover_line, hover_col) if hover_fn else None
            node["signature"] = _parse_hover_signature(hover)
        except Exception:
            node["signature"] = None

    # Recurse into children.
    children_raw = sym.get("children", [])
    node["children"] = [
        _convert_symbol(c, hover_fn=hover_fn, relpath=relpath)
        for c in children_raw
    ]

    return node


def _build_file_tree(relpath: str, symbols: list, total_lines: int,
                     hover_fn=None) -> dict:
    """Build a File root node wrapping the converted symbol children."""
    children = [
        _convert_symbol(sym, hover_fn=hover_fn, relpath=relpath)
        for sym in symbols
    ]
    return {
        "name": relpath,
        "kind": "File",
        "start_line": 1,
        "end_line": total_lines,
        "line_count": total_lines,
        "children": children,
    }


def extract_file_symbols(client: LspClient, relpath: str,
                         signatures: bool = True) -> dict:
    """Extract the full symbol tree for a single file.

    Returns a dict with "file" (relpath) and "root" (the File node).
    """
    symbols = client.document_symbols(relpath)
    if symbols is None:
        symbols = []

    # Count lines in the file for the root node.
    abspath = os.path.join(client.project_root, relpath)
    try:
        with open(abspath) as f:
            total_lines = sum(1 for _ in f)
    except OSError:
        total_lines = 0

    hover_fn = client.hover if signatures else None
    root = _build_file_tree(relpath, symbols, total_lines, hover_fn=hover_fn)
    client.close_file(relpath)
    return {"file": relpath, "root": root}


def extract_symbols_iter(client: LspClient, file_relpaths: list[str],
                         signatures: bool = True):
    """Yield symbol trees one file at a time (for streaming)."""
    total = len(file_relpaths)
    for i, relpath in enumerate(file_relpaths, 1):
        print(f"[{i}/{total}] {relpath}", file=sys.stderr)
        yield extract_file_symbols(client, relpath, signatures=signatures)


def _find_workspace_rs_files(project_root: str) -> list[str]:
    """Find .rs files belonging to workspace crates only (not dependencies).

    Uses `cargo metadata --no-deps` to discover workspace crate source
    directories, then walks only those directories.
    """
    result = subprocess.run(
        ["cargo", "metadata", "--no-deps", "--format-version", "1"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"cargo metadata failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    metadata = json.loads(result.stdout)
    workspace_root = metadata["workspace_root"]
    if not workspace_root.endswith("/"):
        workspace_root += "/"

    ws_member_ids = set(metadata.get("workspace_members", []))

    # Collect workspace crate source directories.
    src_dirs = set()
    for pkg in metadata.get("packages", []):
        if pkg["id"] not in ws_member_ids:
            continue
        for target in pkg.get("targets", []):
            src_path = target.get("src_path", "")
            if src_path:
                # src_path points to the root source file (e.g. src/lib.rs);
                # walk its parent directory.
                src_dir = os.path.dirname(src_path)
                # Walk up to the crate root (directory containing Cargo.toml).
                manifest_dir = os.path.dirname(pkg["manifest_path"])
                src_dirs.add(manifest_dir)

    rs_files = []
    for src_dir in sorted(src_dirs):
        for dirpath, dirs, filenames in os.walk(src_dir):
            # Skip target directories and hidden directories.
            dirs[:] = [d for d in dirs if d != "target" and not d.startswith(".")]
            for fn in filenames:
                if fn.endswith(".rs"):
                    abspath = os.path.join(dirpath, fn)
                    relpath = os.path.relpath(abspath, project_root)
                    rs_files.append(relpath)

    rs_files.sort()
    return rs_files


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    args = sys.argv[1:]
    signatures = True
    if "--no-signatures" in args:
        args.remove("--no-signatures")
        signatures = False

    if args and args[0] == "--all-rs":
        files = _find_workspace_rs_files(PROJECT_ROOT)
        if not files:
            print("No .rs files found in workspace crates.", file=sys.stderr)
            sys.exit(1)
        print(f"Found {len(files)} .rs files in workspace crates.", file=sys.stderr)
    else:
        files = args

    if not files:
        print(__doc__)
        sys.exit(1)

    mode = "with signatures" if signatures else "without signatures (no hover)"
    scope = f" (crate: {RA_SINGLE_CRATE})" if RA_SINGLE_CRATE else " (full workspace)"
    print(f"Connecting to rust-analyzer for project: {PROJECT_ROOT}{scope}",
          file=sys.stderr)
    print(f"Mode: {mode}", file=sys.stderr)

    client = LspClient(PROJECT_ROOT, single_crate=RA_SINGLE_CRATE)
    client.initialize()
    print("Ready.", file=sys.stderr)

    try:
        for record in extract_symbols_iter(client, files, signatures=signatures):
            print(json.dumps(record))
            sys.stdout.flush()
    finally:
        client.shutdown()


if __name__ == "__main__":
    main()
