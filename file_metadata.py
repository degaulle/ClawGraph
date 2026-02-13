import os


def get_file_type(path: str) -> str | None:
    """Extract extension from path. Returns None for files without extension."""
    basename = os.path.basename(path)
    # Hidden files with no further extension (e.g. .gitignore)
    if basename.startswith(".") and "." not in basename[1:]:
        return None
    _, ext = os.path.splitext(basename)
    if ext:
        return ext[1:]  # Remove the leading dot
    return None


def get_line_count(file_path: str) -> int | None:
    """Count lines in a file. Returns None if file doesn't exist."""
    try:
        with open(file_path, "r", errors="replace") as f:
            return sum(1 for _ in f)
    except (FileNotFoundError, IsADirectoryError):
        return None


def enrich_file_nodes(file_nodes: list[dict], repo_path: str) -> None:
    """Mutates file_nodes in place, adding latest_line_count and file_type."""
    for node in file_nodes:
        node["file_type"] = get_file_type(node["name"])
        if node["deleted"]:
            node["latest_line_count"] = None
        else:
            full_path = os.path.join(repo_path, node["name"])
            node["latest_line_count"] = get_line_count(full_path)
