"""Extract crate nodes and edges from a Cargo workspace using `cargo metadata`."""

import json
import subprocess


def _run_cargo_metadata(workspace_path: str) -> dict:
    """Run cargo metadata and return parsed JSON."""
    result = subprocess.run(
        ["cargo", "metadata", "--no-deps", "--format-version", "1"],
        cwd=workspace_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cargo metadata failed: {result.stderr}")
    return json.loads(result.stdout)


def _parse_metadata(metadata: dict) -> list[dict]:
    """Parse cargo metadata JSON into crate node dicts.

    Filters to workspace members only. Paths are made relative to workspace_root.
    """
    workspace_root = metadata["workspace_root"]
    # Normalise: ensure trailing slash for prefix stripping
    if not workspace_root.endswith("/"):
        workspace_root += "/"

    workspace_member_ids = set(metadata.get("workspace_members", []))

    # Collect workspace packages
    ws_packages = []
    for pkg in metadata.get("packages", []):
        if pkg["id"] in workspace_member_ids:
            ws_packages.append(pkg)

    # Sort by name for deterministic IDs
    ws_packages.sort(key=lambda p: p["name"])

    crate_nodes = []
    for idx, pkg in enumerate(ws_packages, start=1):
        manifest_path = pkg["manifest_path"]
        if manifest_path.startswith(workspace_root):
            manifest_rel = manifest_path[len(workspace_root):]
        else:
            manifest_rel = manifest_path

        # root_dir is the directory containing Cargo.toml, relative to workspace
        if "/" in manifest_rel:
            root_dir = manifest_rel.rsplit("/", 1)[0] + "/"
        else:
            # Cargo.toml at workspace root
            root_dir = ""

        targets = pkg.get("targets", [])
        has_lib = any(
            "lib" in t.get("kind", []) or "proc-macro" in t.get("kind", [])
            for t in targets
        )
        has_bin = any("bin" in t.get("kind", []) for t in targets)

        crate_nodes.append({
            "id": f"crate_{idx}",
            "name": pkg["name"],
            "root_dir": root_dir,
            "manifest_path": manifest_rel,
            "edition": pkg.get("edition", "2021"),
            "has_lib": has_lib,
            "has_bin": has_bin,
        })

    return crate_nodes


def extract_crates(workspace_path: str) -> list[dict]:
    """Extract crate nodes from a Cargo workspace.

    Runs `cargo metadata --no-deps --format-version 1` and returns crate node dicts.
    """
    metadata = _run_cargo_metadata(workspace_path)
    return _parse_metadata(metadata)


def _build_dependency_edges_from_metadata(
    metadata: dict, crate_nodes: list[dict]
) -> list[dict]:
    """Build depends_on edges from cargo metadata JSON and crate nodes."""
    workspace_root = metadata["workspace_root"]
    if not workspace_root.endswith("/"):
        workspace_root += "/"

    workspace_member_ids = set(metadata.get("workspace_members", []))

    # Map crate name -> crate node id
    name_to_id = {c["name"]: c["id"] for c in crate_nodes}

    # Map package name for workspace members
    ws_package_names = set()
    for pkg in metadata.get("packages", []):
        if pkg["id"] in workspace_member_ids:
            ws_package_names.add(pkg["name"])

    edges = []
    for pkg in metadata.get("packages", []):
        if pkg["id"] not in workspace_member_ids:
            continue
        source_id = name_to_id.get(pkg["name"])
        if source_id is None:
            continue
        for dep in pkg.get("dependencies", []):
            dep_name = dep["name"]
            # Only include edges to other workspace crates
            if dep_name in ws_package_names and dep_name in name_to_id:
                target_id = name_to_id[dep_name]
                if source_id != target_id:  # no self-edges
                    edges.append({
                        "source": source_id,
                        "target": target_id,
                        "type": "depends_on",
                    })

    return edges


def build_crate_dependency_edges(
    workspace_path: str, crate_nodes: list[dict]
) -> list[dict]:
    """Build crate-to-crate dependency edges for workspace members.

    Returns list of {"source": "crate_X", "target": "crate_Y", "type": "depends_on"}.
    """
    metadata = _run_cargo_metadata(workspace_path)
    return _build_dependency_edges_from_metadata(metadata, crate_nodes)


def map_files_to_crates(
    file_nodes: list[dict], crate_nodes: list[dict]
) -> list[dict]:
    """Map file nodes to the crate whose root_dir is the longest prefix match.

    Files outside any crate's root_dir get no mapping.
    Returns list of {"source": "crate_X", "target": "file_Y", "type": "contains"}.
    """
    # Sort crate_nodes by root_dir length descending for longest-prefix-first matching
    sorted_crates = sorted(crate_nodes, key=lambda c: len(c["root_dir"]), reverse=True)

    edges = []
    for fnode in file_nodes:
        file_path = fnode["name"]
        for crate in sorted_crates:
            root_dir = crate["root_dir"]
            if not root_dir:
                # Empty root_dir means workspace root; matches everything,
                # but we only use it as last resort (it's the shortest prefix).
                # Since we iterate longest-first, this is correct.
                edges.append({
                    "source": crate["id"],
                    "target": fnode["id"],
                    "type": "contains",
                })
                break
            if file_path.startswith(root_dir):
                edges.append({
                    "source": crate["id"],
                    "target": fnode["id"],
                    "type": "contains",
                })
                break
        # If no crate matched, file is unmapped (e.g. workspace-root files)

    return edges
