"""Extract concept nodes and edges from a concept-map YAML and tag/summary JSON files."""

import json

import yaml


def extract_concepts(yaml_path: str) -> tuple[list[dict], list[dict]]:
    """Extract major and minor concept nodes from a concept-map YAML file.

    Major concepts are sorted by name, assigned IDs major_concept_1..N.
    Minor concepts are sorted by (major_name, minor_name), assigned IDs minor_concept_1..N.

    Returns:
        (major_concept_nodes, minor_concept_nodes)
    """
    with open(yaml_path, "r") as f:
        data = yaml.safe_load(f)

    raw_concepts = data.get("concepts", [])

    # Sort major concepts by name for stable IDs
    sorted_majors = sorted(raw_concepts, key=lambda c: c["major"])

    major_nodes = []
    # Collect (major_name, major_id, minor_entry) tuples for sorting
    minor_entries = []

    for idx, entry in enumerate(sorted_majors, start=1):
        major_id = f"major_concept_{idx}"
        major_nodes.append({
            "id": major_id,
            "name": entry["major"],
            "definition": entry.get("definition", "").strip(),
            "evidence": entry.get("evidence", []) or [],
        })

        for m in entry.get("minor", None) or []:
            minor_entries.append((entry["major"], m.get("concept", ""), major_id, m))

    # Sort minor concepts by (major_name, minor_name) for stable IDs
    minor_entries.sort(key=lambda t: (t[0], t[1]))

    minor_nodes = []
    for idx, (_, minor_name, major_id, m) in enumerate(minor_entries, start=1):
        minor_nodes.append({
            "id": f"minor_concept_{idx}",
            "name": minor_name,
            "definition": m.get("definition", "").strip(),
            "evidence": m.get("evidence", []) or [],
            "major_concept": major_id,
        })

    return major_nodes, minor_nodes


def build_concept_hierarchy_edges(
    major_concepts: list[dict],
    minor_concepts: list[dict],
) -> list[dict]:
    """Build has_minor edges from each minor concept's major_concept field.

    Returns:
        [{source: major_id, target: minor_id, type: "has_minor"}, ...]
    """
    return [
        {
            "source": m["major_concept"],
            "target": m["id"],
            "type": "has_minor",
        }
        for m in minor_concepts
    ]


def build_concept_file_edges(
    tag_json_path: str,
    major_concepts: list[dict],
    minor_concepts: list[dict],
    file_nodes: list[dict],
) -> list[dict]:
    """Build tagged_with edges from a tag JSON file.

    Parses each file entry's JSON-encoded response to extract concept tags,
    then matches tag names against concept names and source_path against
    file node names.

    Returns:
        [{source: concept_id, target: file_id, type: "tagged_with"}, ...]
    """
    with open(tag_json_path, "r") as f:
        tag_data = json.load(f)

    # Build name→id lookups
    concept_lookup: dict[str, str] = {}
    for c in major_concepts:
        concept_lookup[c["name"]] = c["id"]
    for c in minor_concepts:
        concept_lookup[c["name"]] = c["id"]

    file_lookup: dict[str, str] = {}
    for fn in file_nodes:
        file_lookup[fn["name"]] = fn["id"]

    seen: set[tuple[str, str]] = set()
    edges: list[dict] = []

    for entry in tag_data.get("files", []):
        source_path = entry.get("source_path", "")
        file_id = file_lookup.get(source_path)
        if file_id is None:
            continue

        try:
            tags = json.loads(entry["response"]).get("tags", [])
        except (json.JSONDecodeError, KeyError):
            continue

        for tag in tags:
            concept_id = concept_lookup.get(tag)
            if concept_id is None:
                continue
            pair = (concept_id, file_id)
            if pair in seen:
                continue
            seen.add(pair)
            edges.append({
                "source": concept_id,
                "target": file_id,
                "type": "tagged_with",
            })

    return edges


def enrich_file_summaries(
    file_nodes: list[dict],
    summary_json_path: str,
) -> None:
    """Mutate file nodes in place, adding a 'summary' field from a summary JSON file.

    Files not found in the summary data get summary=None.
    """
    with open(summary_json_path, "r") as f:
        summary_data = json.load(f)

    summary_lookup: dict[str, str] = {}
    for entry in summary_data.get("files", []):
        summary_lookup[entry["source_path"]] = entry["response"]

    for node in file_nodes:
        node["summary"] = summary_lookup.get(node["name"])


def _parse_json_response(response: str) -> dict | None:
    """Extract a JSON object from a response that may be wrapped in markdown fences."""
    text = response.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Drop opening fence (```json or ```) and closing fence (```)
        lines = [l for l in lines[1:] if not l.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def enrich_contributor_summaries(
    contributor_nodes: list[dict],
    summary_json_path: str,
) -> None:
    """Mutate contributor nodes in place, adding 'role' and 'contributions' fields."""
    with open(summary_json_path, "r") as f:
        summary_data = json.load(f)

    # source_path is like "contributor_1.txt" → key on "contributor_1"
    summary_lookup: dict[str, dict] = {}
    for entry in summary_data.get("files", []):
        key = entry["source_path"].removesuffix(".txt")
        parsed = _parse_json_response(entry["response"])
        if parsed:
            summary_lookup[key] = parsed

    for node in contributor_nodes:
        info = summary_lookup.get(node["id"])
        if info:
            node["role"] = info.get("role")
            node["contributions"] = info.get("contributions")
