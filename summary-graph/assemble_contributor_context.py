#!/usr/bin/env python3
"""Assemble per-contributor context files from the knowledge graph for batch summarization.

Reads knowledge_graph.json and writes one .txt file per contributor into an output directory,
containing structured context suitable for the summarize_contributor.template prompt.

Usage:
    python assemble_contributor_context.py --graph ../output/knowledge_graph.json --output ./contributor_contexts/
"""

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path


def load_graph(graph_path: str) -> dict:
    with open(graph_path) as f:
        return json.load(f)


def assemble_context(graph: dict, contributor: dict) -> str:
    """Assemble a structured context string for one contributor."""
    cid = contributor["id"]

    # Build edge indexes on first call (cached on the graph dict).
    if "_idx" not in graph:
        idx = {}
        idx["authored"] = defaultdict(list)
        idx["contributed_to"] = defaultdict(list)
        idx["tagged_with_target"] = defaultdict(list)
        for e in graph["edges"]:
            if e["type"] == "authored":
                idx["authored"][e["source"]].append(e)
            elif e["type"] == "contributed_to":
                idx["contributed_to"][e["source"]].append(e)
            elif e["type"] == "tagged_with":
                idx["tagged_with_target"][e["target"]].append(e)
        idx["file_lookup"] = {f["id"]: f for f in graph["nodes"]["files"]}
        idx["crate_lookup"] = {c["id"]: c["name"] for c in graph["nodes"].get("crates", [])}
        idx["concept_lookup"] = {}
        for mc in graph["nodes"].get("major_concepts", []):
            idx["concept_lookup"][mc["id"]] = ("major", mc["name"])
        for mc in graph["nodes"].get("minor_concepts", []):
            idx["concept_lookup"][mc["id"]] = ("minor", mc["name"])
        graph["_idx"] = idx

    idx = graph["_idx"]

    # 1. Crates contributed to
    ct_edges = idx["contributed_to"].get(cid, [])
    crate_contributions = []
    for e in sorted(ct_edges, key=lambda e: -e.get("total_commits", 0)):
        crate_name = idx["crate_lookup"].get(e["target"], e["target"])
        crate_contributions.append(
            f"  - {crate_name}: {e['total_commits']} commits (since {e['first_contribution_at'][:10]})"
        )

    # 2. Authored files and their types
    auth_edges = idx["authored"].get(cid, [])
    file_type_counts = Counter()
    file_summaries_sample = []
    authored_file_ids = set()
    for ae in auth_edges:
        fnode = idx["file_lookup"].get(ae["target"])
        if fnode:
            authored_file_ids.add(ae["target"])
            ft = fnode.get("file_type") or "unknown"
            file_type_counts[ft] += 1
            if fnode.get("summary") and len(file_summaries_sample) < 10:
                file_summaries_sample.append(f"  - {fnode['name']}: {fnode['summary']}")

    # 3. Concepts touched (via tagged_with on authored files)
    concept_counts = Counter()
    for fid in authored_file_ids:
        for tw in idx["tagged_with_target"].get(fid, []):
            concept_counts[tw["source"]] += 1

    major_concepts = []
    minor_concepts = []
    for concept_id, count in concept_counts.most_common():
        level, name = idx["concept_lookup"].get(concept_id, ("?", concept_id))
        if level == "major":
            major_concepts.append(f"  - {name} ({count} files)")
        elif level == "minor":
            minor_concepts.append(f"  - {name} ({count} files)")

    # 4. Sample commit messages (up to 20)
    all_commit_hashes = set()
    for ae in auth_edges:
        all_commit_hashes.update(ae.get("commits", []))

    commit_samples = []
    commits_dict = graph.get("commits", {})
    for h in sorted(all_commit_hashes)[:20]:
        c = commits_dict.get(h)
        if c:
            commit_samples.append(f"  - {c['message']}")

    # 5. Assemble the context string
    lines = []
    lines.append(f"Name: {contributor['name']}")
    lines.append(f"Total commits: {contributor['total_commits']}")
    lines.append(f"First contribution: {contributor['first_commit_at']}")
    lines.append(f"Emails: {', '.join(contributor['emails'])}")
    lines.append(f"Files authored: {len(auth_edges)}")
    lines.append("")

    lines.append("File type distribution:")
    for ft, count in file_type_counts.most_common(10):
        lines.append(f"  - .{ft}: {count} files")
    lines.append("")

    if crate_contributions:
        lines.append(f"Packages contributed to ({len(crate_contributions)} total):")
        for line in crate_contributions[:15]:
            lines.append(line)
        lines.append("")

    if major_concepts:
        lines.append("Major product areas touched:")
        for line in major_concepts:
            lines.append(line)
        lines.append("")

    if minor_concepts:
        lines.append(f"Specific features touched ({len(minor_concepts)} total, top 15):")
        for line in minor_concepts[:15]:
            lines.append(line)
        lines.append("")

    if file_summaries_sample:
        lines.append("Sample of authored files (with descriptions):")
        for line in file_summaries_sample:
            lines.append(line)
        lines.append("")

    if commit_samples:
        lines.append("Sample commit messages:")
        for line in commit_samples:
            lines.append(line)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Assemble per-contributor context files from the knowledge graph."
    )
    parser.add_argument("--graph", required=True, help="Path to knowledge_graph.json")
    parser.add_argument("--output", required=True, help="Output directory for context files")
    parser.add_argument(
        "--min-commits", type=int, default=1,
        help="Minimum commits to generate context (default: 1)",
    )
    args = parser.parse_args()

    graph = load_graph(args.graph)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    contributors = graph["nodes"]["contributors"]
    count = 0
    for contributor in contributors:
        if contributor["total_commits"] < args.min_commits:
            continue
        context = assemble_context(graph, contributor)
        out_path = out_dir / f"{contributor['id']}.txt"
        with open(out_path, "w") as f:
            f.write(context)
        count += 1

    print(f"Wrote {count} contributor context files to {out_dir}")


if __name__ == "__main__":
    main()
