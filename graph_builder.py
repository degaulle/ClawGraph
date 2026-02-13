from datetime import datetime, timezone

from rename_tracker import RenameTracker
from file_metadata import enrich_file_nodes


def build_graph(parsed_commits: list[dict], repo_path: str) -> dict:
    """
    Build the knowledge graph from parsed commits.

    Returns:
    {
        "nodes": {"files": [...], "contributors": [...]},
        "edges": [...],
        "commits": {...}
    }
    """
    tracker = RenameTracker()

    # contributor_name -> contributor data
    contributors: dict[str, dict] = {}
    contributor_counter = 0

    # (contributor_id, file_id) -> [commit_hashes]
    edge_map: dict[tuple[str, str], list[str]] = {}

    # commit_hash -> commit data
    commits_lookup: dict[str, dict] = {}

    for commit in parsed_commits:
        author_name = commit["author"]
        email = commit["email"]
        timestamp = commit["timestamp"]
        iso_ts = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()

        # Build/update contributor
        if author_name not in contributors:
            contributor_counter += 1
            contributor_id = f"contributor_{contributor_counter}"
            contributors[author_name] = {
                "id": contributor_id,
                "name": author_name,
                "emails": [email],
                "first_commit_at": iso_ts,
                "total_commits": 0,
            }

        contributor = contributors[author_name]
        contributor["total_commits"] += 1
        if email not in contributor["emails"]:
            contributor["emails"].append(email)

        contributor_id = contributor["id"]

        # Build commit lookup
        commits_lookup[commit["hash"]] = {
            "message": commit["message"],
            "author": contributor_id,
            "timestamp": iso_ts,
        }

        # Process file changes
        for change in commit["changes"]:
            file_id = tracker.process_change(change, timestamp)
            edge_key = (contributor_id, file_id)
            if edge_key not in edge_map:
                edge_map[edge_key] = []
            edge_map[edge_key].append(commit["hash"])

    # Get file nodes and convert timestamps to ISO 8601
    file_nodes = tracker.get_file_nodes()
    for node in file_nodes:
        node["created_at"] = datetime.fromtimestamp(
            node["created_at"], tz=timezone.utc
        ).isoformat()
        node["last_modified_at"] = datetime.fromtimestamp(
            node["last_modified_at"], tz=timezone.utc
        ).isoformat()

    # Enrich with file metadata
    enrich_file_nodes(file_nodes, repo_path)

    # Collapse edge dict into edge list
    edges = [
        {"source": src, "target": tgt, "type": "authored", "commits": hashes}
        for (src, tgt), hashes in edge_map.items()
    ]

    return {
        "nodes": {
            "files": file_nodes,
            "contributors": list(contributors.values()),
        },
        "edges": edges,
        "commits": commits_lookup,
    }
