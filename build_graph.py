#!/usr/bin/env python3
"""Main entry point: builds a knowledge graph from the codex repository's git history."""

import json
import os
import subprocess
import sys

from git_log_parser import parse_git_log
from graph_builder import build_graph


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

    # Write output
    output_path = os.path.join(script_dir, "knowledge_graph.json")
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
