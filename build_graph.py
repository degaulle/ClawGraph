#!/usr/bin/env python3
"""Main entry point: builds a knowledge graph from the codex repository's git history."""

import json
import os
import subprocess
import sys

from git_log_parser import parse_git_log
from graph_builder import build_graph

# Allow importing from rust-graph/
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "rust-graph"))
from crate_extractor import extract_crates, build_crate_dependency_edges, map_files_to_crates, enrich_crate_created_at, build_contributor_crate_edges


def _find_cargo_workspaces(repo_path: str) -> list[str]:
    """Find directories containing a Cargo.toml with a [workspace] section.

    Searches the repo root and one level of subdirectories.
    Returns absolute paths to workspace roots.
    """
    workspaces = []
    candidates = [repo_path]
    # Also check immediate subdirectories (e.g. codex-rs/)
    try:
        for entry in os.scandir(repo_path):
            if entry.is_dir() and not entry.name.startswith("."):
                candidates.append(entry.path)
    except OSError:
        pass

    for candidate in candidates:
        cargo_toml = os.path.join(candidate, "Cargo.toml")
        if not os.path.isfile(cargo_toml):
            continue
        with open(cargo_toml, "r") as f:
            for line in f:
                if line.strip().startswith("[workspace"):
                    workspaces.append(candidate)
                    break
    return workspaces


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_path = os.path.normpath(os.path.join(script_dir, "..", "codex"))

    if not os.path.isdir(os.path.join(repo_path, ".git")):
        print(f"Error: {repo_path} is not a git repository", file=sys.stderr)
        sys.exit(1)

    # Run git log
    result = subprocess.run(
        [
            "git", "log", "--first-parent", "--reverse",
            "--format=COMMIT_START%n%H%n%aN%n%aE%n%at%n%s",
            "--name-status", "-M",
        ],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"Error running git log: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    # Parse and build
    parsed_commits = parse_git_log(result.stdout)
    graph = build_graph(parsed_commits, repo_path)

    # Add crate layer for each Cargo workspace found
    all_crate_nodes = []
    all_dep_edges = []
    all_contains_edges = []
    workspaces = _find_cargo_workspaces(repo_path)
    crate_id_offset = 0

    for ws_path in workspaces:
        crate_nodes = extract_crates(ws_path)

        # If workspace is a subdirectory, prefix root_dir and manifest_path
        # so they are relative to repo_path (matching file node names).
        ws_rel = os.path.relpath(ws_path, repo_path)
        if ws_rel != ".":
            prefix = ws_rel + "/"
            for cnode in crate_nodes:
                cnode["root_dir"] = prefix + cnode["root_dir"]
                cnode["manifest_path"] = prefix + cnode["manifest_path"]

        # Re-number crate IDs to avoid collisions across workspaces
        if crate_id_offset > 0:
            id_remap = {}
            for cnode in crate_nodes:
                old_id = cnode["id"]
                num = int(old_id.split("_")[1]) + crate_id_offset
                new_id = f"crate_{num}"
                id_remap[old_id] = new_id
                cnode["id"] = new_id

        enrich_crate_created_at(crate_nodes, graph["nodes"]["files"])
        dep_edges = build_crate_dependency_edges(ws_path, crate_nodes)
        contains_edges = map_files_to_crates(graph["nodes"]["files"], crate_nodes)

        all_crate_nodes.extend(crate_nodes)
        all_dep_edges.extend(dep_edges)
        all_contains_edges.extend(contains_edges)
        crate_id_offset += len(crate_nodes)

    if all_crate_nodes:
        graph["nodes"]["crates"] = all_crate_nodes
        graph["edges"].extend(all_dep_edges)
        graph["edges"].extend(all_contains_edges)

        contributed_to_edges = build_contributor_crate_edges(
            graph["edges"], all_contains_edges, graph["commits"]
        )
        graph["edges"].extend(contributed_to_edges)

        print(f"  Crates: {len(all_crate_nodes)}")
        print(f"  depends_on edges: {len(all_dep_edges)}")
        print(f"  contains edges: {len(all_contains_edges)}")
        print(f"  contributed_to edges: {len(contributed_to_edges)}")

    # Write output
    output_dir = os.path.join(script_dir, "output")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "knowledge_graph.json")
    with open(output_path, "w") as f:
        json.dump(graph, f, indent=2)

    # Print summary
    n_files = len(graph["nodes"]["files"])
    n_contributors = len(graph["nodes"]["contributors"])
    n_edges = len(graph["edges"])
    n_commits = len(graph["commits"])
    print(f"Knowledge graph built successfully:")
    print(f"  Files: {n_files}")
    print(f"  Contributors: {n_contributors}")
    print(f"  Edges: {n_edges}")
    print(f"  Commits: {n_commits}")
    print(f"  Output: {output_path}")


if __name__ == "__main__":
    main()
