"""AI-powered enrichment for dynamically built knowledge graphs.

Uses the Anthropic API to generate concept maps, file summaries,
concept tags, and contributor summaries for arbitrary repositories.
"""

import json
import logging
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

# Ensure summary-graph/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "summary-graph"))
from summarize import summarize_content

from concept_extractor import extract_concepts_from_data

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
TEMPLATE_DIR = ROOT / "summary-graph" / "template"

MAX_SUMMARIZE_FILES = 300
MAX_FILE_SIZE = 50_000  # bytes
MAX_CONTRIBUTORS = 50
SUMMARY_MODEL = "claude-haiku-4-5-20251001"
CONCEPT_MAP_MODEL = "claude-haiku-4-5-20251001"
WORKERS = 20

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp", ".bmp",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z",
    ".bin", ".exe", ".dll", ".so", ".dylib", ".o", ".a",
    ".lock", ".min.js", ".min.css", ".map",
    ".mp3", ".mp4", ".wav", ".avi", ".mov",
    ".pyc", ".pyo", ".class", ".jar",
}

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "target", "vendor", "dist", "build", ".next", ".nuxt",
    "coverage", ".tox", ".mypy_cache", ".pytest_cache",
}


def _has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _dir_tree(repo_dir: str, max_depth: int = 3, max_lines: int = 200) -> str:
    """Build a directory tree string for the repo."""
    lines = []
    root = Path(repo_dir)

    def walk(path: Path, prefix: str, depth: int):
        if depth > max_depth or len(lines) >= max_lines:
            return
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
        except PermissionError:
            return
        dirs = [e for e in entries if e.is_dir() and e.name not in SKIP_DIRS]
        files = [e for e in entries if e.is_file()]
        for f in files[:20]:  # cap files per dir
            if len(lines) >= max_lines:
                return
            lines.append(f"{prefix}{f.name}")
        if len(files) > 20:
            lines.append(f"{prefix}... ({len(files) - 20} more files)")
        for d in dirs:
            if len(lines) >= max_lines:
                return
            lines.append(f"{prefix}{d.name}/")
            walk(d, prefix + "  ", depth + 1)

    walk(root, "", 0)
    return "\n".join(lines)


def _top_files_by_edits(file_nodes: list[dict], edges: list[dict], n: int = 100) -> list[str]:
    """Return the top N most-edited file paths (by authored edge count)."""
    edge_count: Counter = Counter()
    for e in edges:
        if e.get("type") == "authored":
            edge_count[e["target"]] += len(e.get("commits", []))
    file_id_to_name = {f["id"]: f["name"] for f in file_nodes if not f.get("deleted")}
    ranked = sorted(file_id_to_name.keys(), key=lambda fid: edge_count.get(fid, 0), reverse=True)
    return [file_id_to_name[fid] for fid in ranked[:n]]


def _read_readme(repo_dir: str) -> str:
    """Read the README file from the repo, or return empty string."""
    for name in ("README.md", "README.rst", "README.txt", "README"):
        p = os.path.join(repo_dir, name)
        if os.path.isfile(p):
            try:
                with open(p, "r", errors="replace") as f:
                    content = f.read()
                # Cap at 5000 chars
                return content[:5000]
            except OSError:
                pass
    return ""


def _select_files(file_nodes: list[dict], edges: list[dict], max_files: int) -> list[dict]:
    """Select non-deleted, non-binary files sorted by importance."""
    edge_count: Counter = Counter()
    for e in edges:
        if e.get("type") == "authored":
            edge_count[e["target"]] += len(e.get("commits", []))

    candidates = []
    for f in file_nodes:
        if f.get("deleted"):
            continue
        ext = os.path.splitext(f["name"])[1].lower()
        if ext in BINARY_EXTENSIONS:
            continue
        candidates.append(f)

    candidates.sort(key=lambda f: edge_count.get(f["id"], 0), reverse=True)
    return candidates[:max_files]


# ── Concept Map Generation ───────────────────────────────────────────

def generate_concept_map(
    repo_dir: str,
    repo_name: str,
    file_nodes: list[dict],
    edges: list[dict],
) -> tuple[list[dict], list[dict], str]:
    """Auto-generate a concept map for a repository using Claude.

    Returns (major_concepts, minor_concepts, definitions_str).
    On failure returns ([], [], "").
    """
    if not _has_api_key():
        return [], [], ""

    import anthropic

    readme = _read_readme(repo_dir)
    tree = _dir_tree(repo_dir)
    top_files = _top_files_by_edits(file_nodes, edges, n=100)

    prompt = f"""Context
You are analyzing the {repo_name} repository. Produce a product/feature concept map that becomes the shared coordination vocabulary for Engineering, Product, and UX.

Definition of "concept"
A concept is something a PM/UX/Eng would discuss as a capability, workflow, user goal, constraint, surface area, integration, or lifecycle stage (not a class, package, or internal refactor). Bias towards product and UX thinking.

Method
- Read the README and directory structure below.
- Identify user-facing features, workflows, and integrations.
- Use your product and UX thinking to identify concepts.

Deliverable
- <10 Major Concepts, <100 Minor Concepts total.
- Each Minor Concept has exactly one parent Major Concept.
- For each Major/Minor:
  - Definition (1-2 sentences)
  - Evidence links: some file paths from the list below.

Output: ONLY valid YAML (no markdown fences). Use this exact structure:

concepts:
  - major: "Name"
    definition: "..."
    evidence:
      - path/to/file
    minor:
      - concept: "Name"
        definition: "..."
        evidence:
          - path/to/file

README:
{readme if readme else "(No README found)"}

Directory structure:
{tree}

Top files by activity:
{chr(10).join(top_files[:80])}
"""

    try:
        client = anthropic.Anthropic()
        message = client.messages.create(
            model=CONCEPT_MAP_MODEL,
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
        response = message.content[0].text.strip()

        # Strip markdown fences if present
        if response.startswith("```"):
            lines = response.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            response = "\n".join(lines)

        data = yaml.safe_load(response)
        if not data or "concepts" not in data:
            log.warning("Concept map response missing 'concepts' key")
            return [], [], ""

        major_concepts, minor_concepts = extract_concepts_from_data(data)

        # Build definitions-only string for tagging
        defs_lines = []
        for entry in data["concepts"]:
            defs_lines.append(f"Major: {entry['major']}")
            defs_lines.append(f"  Definition: {entry.get('definition', '')}")
            for m in entry.get("minor", []) or []:
                defs_lines.append(f"  Minor: {m.get('concept', '')}")
                defs_lines.append(f"    Definition: {m.get('definition', '')}")
        definitions_str = "\n".join(defs_lines)

        log.info("Generated concept map: %d major, %d minor concepts",
                 len(major_concepts), len(minor_concepts))
        return major_concepts, minor_concepts, definitions_str

    except Exception as e:
        log.error("Concept map generation failed: %s", e)
        return [], [], ""


# ── File Summarization ───────────────────────────────────────────────

def batch_summarize_files(
    repo_dir: str,
    file_nodes: list[dict],
    edges: list[dict],
    progress_cb=None,
    max_files: int = MAX_SUMMARIZE_FILES,
) -> list[dict]:
    """Summarize files using Claude. Returns list of {source_path, response}."""
    if not _has_api_key():
        return []

    template = (TEMPLATE_DIR / "summarize_file.template").read_text()
    # Generalize template
    template = template.replace("OpenAI Codex repo", "this repository")

    selected = _select_files(file_nodes, edges, max_files)
    results = []
    completed = 0
    total = len(selected)

    def summarize_one(file_node):
        fpath = os.path.join(repo_dir, file_node["name"])
        try:
            with open(fpath, "r", errors="replace") as f:
                content = f.read()
            if len(content) > MAX_FILE_SIZE:
                return None
        except (OSError, IsADirectoryError):
            return None
        try:
            result = summarize_content(
                template,
                {"%FILE_CONTENT%": content, "%FILE_PATH%": file_node["name"]},
                model=SUMMARY_MODEL,
                max_tokens=512,
            )
            return {"source_path": file_node["name"], "response": result["response"]}
        except Exception as e:
            log.warning("Summary failed for %s: %s", file_node["name"], e)
            return None

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(summarize_one, f): f for f in selected}
        for future in as_completed(futures):
            completed += 1
            result = future.result()
            if result:
                results.append(result)
            if progress_cb and completed % 5 == 0:
                progress_cb(completed, total)

    if progress_cb:
        progress_cb(total, total)
    log.info("Summarized %d/%d files", len(results), total)
    return results


# ── File Concept Tagging ─────────────────────────────────────────────

def batch_tag_files(
    repo_dir: str,
    file_nodes: list[dict],
    edges: list[dict],
    definitions_str: str,
    progress_cb=None,
    max_files: int = MAX_SUMMARIZE_FILES,
) -> list[dict]:
    """Tag files with concepts using Claude. Returns list of {source_path, response}."""
    if not _has_api_key() or not definitions_str:
        return []

    template = (TEMPLATE_DIR / "tag_file.template").read_text()
    template = template.replace("OpenAI Codex repo", "this repository")
    template = template.replace("%CONCEPT_DEFINITIONS%", definitions_str)

    schema_path = TEMPLATE_DIR / "tag_file_schema.json"
    tag_schema = json.loads(schema_path.read_text()) if schema_path.is_file() else None

    selected = _select_files(file_nodes, edges, max_files)
    results = []
    completed = 0
    total = len(selected)

    def tag_one(file_node):
        fpath = os.path.join(repo_dir, file_node["name"])
        try:
            with open(fpath, "r", errors="replace") as f:
                content = f.read()
            if len(content) > MAX_FILE_SIZE:
                return None
        except (OSError, IsADirectoryError):
            return None
        try:
            result = summarize_content(
                template,
                {"%FILE_CONTENT%": content, "%FILE_PATH%": file_node["name"]},
                model=SUMMARY_MODEL,
                max_tokens=512,
                json_schema=tag_schema,
            )
            return {"source_path": file_node["name"], "response": result["response"]}
        except Exception as e:
            log.warning("Tagging failed for %s: %s", file_node["name"], e)
            return None

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(tag_one, f): f for f in selected}
        for future in as_completed(futures):
            completed += 1
            result = future.result()
            if result:
                results.append(result)
            if progress_cb and completed % 5 == 0:
                progress_cb(completed, total)

    if progress_cb:
        progress_cb(total, total)
    log.info("Tagged %d/%d files", len(results), total)
    return results


# ── Contributor Summaries ────────────────────────────────────────────

def batch_summarize_contributors(
    graph: dict,
    progress_cb=None,
    max_contributors: int = MAX_CONTRIBUTORS,
) -> list[dict]:
    """Summarize contributors using Claude. Returns list of {source_path, response}."""
    if not _has_api_key():
        return []

    template = (TEMPLATE_DIR / "summarize_contributor.template").read_text()
    template = template.replace("OpenAI Codex project", "this project")

    schema_path = TEMPLATE_DIR / "summarize_contributor_schema.json"
    contrib_schema = json.loads(schema_path.read_text()) if schema_path.is_file() else None

    contributors = sorted(
        graph["nodes"]["contributors"],
        key=lambda c: c["total_commits"],
        reverse=True,
    )[:max_contributors]

    # Build file lookup for context assembly
    file_lookup = {f["id"]: f for f in graph["nodes"]["files"]}
    # Build contributor→files from edges
    contrib_files: dict[str, list[str]] = {}
    for e in graph["edges"]:
        if e["type"] == "authored":
            contrib_files.setdefault(e["source"], []).append(e["target"])

    # Build commit messages lookup
    commits = graph.get("commits", {})

    results = []
    completed = 0
    total = len(contributors)

    def summarize_one(contributor):
        cid = contributor["id"]
        file_ids = contrib_files.get(cid, [])
        files = [file_lookup[fid] for fid in file_ids if fid in file_lookup]

        # Assemble context
        lines = []
        lines.append(f"Name: {contributor['name']}")
        lines.append(f"Total commits: {contributor['total_commits']}")
        lines.append(f"First contribution: {contributor.get('first_commit_at', 'unknown')}")
        lines.append(f"Files authored: {len(files)}")
        lines.append("")

        # File type distribution
        ext_counts: Counter = Counter()
        for f in files:
            ext = f.get("file_type") or "(none)"
            ext_counts[ext] += 1
        if ext_counts:
            lines.append("File type distribution:")
            for ext, count in ext_counts.most_common(10):
                lines.append(f"  - .{ext}: {count} files")
            lines.append("")

        # Sample files
        sample_files = files[:30]
        if sample_files:
            lines.append("Sample files authored:")
            for f in sample_files:
                summary = f.get("summary") or ""
                if summary:
                    lines.append(f"  - {f['name']}: {summary[:100]}")
                else:
                    lines.append(f"  - {f['name']}")
            lines.append("")

        # Sample commit messages
        commit_msgs = []
        for e in graph["edges"]:
            if e["type"] == "authored" and e["source"] == cid:
                for h in e.get("commits", [])[:5]:
                    c = commits.get(h)
                    if c:
                        commit_msgs.append(c["message"])
        if commit_msgs:
            lines.append("Sample commit messages:")
            for msg in commit_msgs[:20]:
                lines.append(f"  - {msg}")

        context = "\n".join(lines)
        try:
            result = summarize_content(
                template,
                {
                    "%FILE_CONTENT%": context,
                    "%CONTRIBUTOR_NAME%": contributor["name"],
                },
                model=SUMMARY_MODEL,
                max_tokens=512,
                json_schema=contrib_schema,
            )
            return {"source_path": f"{cid}.txt", "response": result["response"]}
        except Exception as e:
            log.warning("Contributor summary failed for %s: %s", contributor["name"], e)
            return None

    with ThreadPoolExecutor(max_workers=min(WORKERS, 10)) as executor:
        futures = {executor.submit(summarize_one, c): c for c in contributors}
        for future in as_completed(futures):
            completed += 1
            result = future.result()
            if result:
                results.append(result)
            if progress_cb:
                progress_cb(completed, total)

    log.info("Summarized %d/%d contributors", len(results), total)
    return results
