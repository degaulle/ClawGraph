"""
Clone a public GitHub repository and build a minimal knowledge graph.

Used by the web server to generate graphs on demand for arbitrary repos.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile

from git_log_parser import parse_git_log
from graph_builder import build_graph


def validate_repo(url_or_shorthand: str) -> tuple[str, str, str]:
    """Parse and validate a GitHub repo reference.

    Accepts:
      - "owner/name"
      - "https://github.com/owner/name"
      - "https://github.com/owner/name.git"
      - "github.com/owner/name"

    Returns (owner, name, clone_url).
    Raises ValueError on invalid input.
    """
    s = url_or_shorthand.strip().rstrip("/")

    # Strip common prefixes
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if s.lower().startswith(prefix):
            s = s[len(prefix):]
            break

    # Remove .git suffix
    if s.endswith(".git"):
        s = s[:-4]

    # Remove query params / fragments
    s = s.split("?")[0].split("#")[0]

    # Should now be "owner/name"
    parts = s.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid repo: expected 'owner/name', got '{url_or_shorthand}'")

    owner, name = parts
    # Validate characters
    pattern = re.compile(r'^[a-zA-Z0-9._-]+$')
    if not pattern.match(owner) or not pattern.match(name):
        raise ValueError(f"Invalid characters in repo: '{owner}/{name}'")

    clone_url = f"https://github.com/{owner}/{name}.git"
    return owner, name, clone_url


def job_id_for(owner: str, name: str) -> str:
    """Sanitized job ID for filesystem and URL use."""
    return f"{owner}_{name}".replace(".", "-")


# Clone phase → (start_pct, end_pct) within the 0.0–0.20 clone band
_CLONE_PHASES = {
    "counting":    (0.00, 0.03),
    "compressing": (0.03, 0.05),
    "receiving":   (0.05, 0.17),
    "resolving":   (0.17, 0.20),
}


def _parse_clone_progress(line: str) -> tuple[str, float] | None:
    """Extract phase name and overall progress (0.0–0.20) from a git clone line."""
    low = line.lower()
    phase = None
    if "counting" in low:
        phase = "counting"
    elif "compressing" in low:
        phase = "compressing"
    elif "receiving" in low:
        phase = "receiving"
    elif "resolving" in low:
        phase = "resolving"
    if not phase:
        return None
    m = re.search(r'(\d+)%', line)
    if not m:
        return None
    pct = int(m.group(1)) / 100.0
    start, end = _CLONE_PHASES[phase]
    return phase, start + pct * (end - start)


def build_repo_graph(
    owner: str,
    name: str,
    clone_url: str,
    output_dir: str,
    progress_cb=None,
) -> str:
    """Clone a GitHub repo and build a minimal knowledge graph.

    Args:
        owner: GitHub owner/org.
        name: Repository name.
        clone_url: HTTPS clone URL.
        output_dir: Directory to write knowledge_graph.json into.
        progress_cb: Optional callback(stage, progress, message).

    Returns:
        Path to the generated knowledge_graph.json.

    Raises:
        RuntimeError on failure.
    """
    def emit(stage, progress, message):
        if progress_cb:
            progress_cb(stage, progress, message)

    tmp_dir = None
    try:
        # --- Stage 1: Clone (0.00 – 0.20) ---
        emit("cloning", 0.0, f"Cloning {owner}/{name}...")
        tmp_dir = tempfile.mkdtemp(prefix="clawgraph_")
        repo_dir = os.path.join(tmp_dir, name)

        proc = subprocess.Popen(
            [
                "git", "clone",
                "--filter=blob:none",
                "--single-branch",
                "--progress",
                clone_url,
                repo_dir,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        buf = b""
        last_progress = 0.0
        while True:
            chunk = proc.stderr.read(256)
            if not chunk:
                break
            buf += chunk
            while b"\r" in buf or b"\n" in buf:
                idx_r = buf.find(b"\r")
                idx_n = buf.find(b"\n")
                if idx_r >= 0 and (idx_n < 0 or idx_r < idx_n):
                    line = buf[:idx_r].decode("utf-8", errors="replace").strip()
                    buf = buf[idx_r + 1:]
                else:
                    line = buf[:idx_n].decode("utf-8", errors="replace").strip()
                    buf = buf[idx_n + 1:]
                if not line:
                    continue
                parsed = _parse_clone_progress(line)
                if parsed:
                    phase, prog = parsed
                    if prog >= last_progress:
                        last_progress = prog
                        label = phase.capitalize()
                        pct_str = re.search(r'(\d+)%', line)
                        pct_display = pct_str.group(1) if pct_str else "?"
                        emit("cloning", prog, f"{label}... {pct_display}%")

        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"git clone failed (exit {proc.returncode})")

        emit("cloning", 0.20, "Clone complete")

        # --- Stage 2: Git log (0.20 – 0.28) ---
        emit("git_log", 0.21, "Running git log...")

        log_proc = subprocess.Popen(
            [
                "git", "log", "--first-parent", "--reverse",
                "--format=COMMIT_START%n%H%n%aN%n%aE%n%at%n%s",
                "--name-status", "-M",
            ],
            cwd=repo_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        raw_chunks = []
        commit_count = 0
        while True:
            chunk = log_proc.stdout.read(65536)
            if not chunk:
                break
            raw_chunks.append(chunk)
            commit_count += chunk.count(b"COMMIT_START")
            if commit_count % 200 == 0 and commit_count > 0:
                emit("git_log", min(0.22 + 0.05, 0.27),
                     f"Reading history... {commit_count:,} commits so far")

        log_proc.wait()
        if log_proc.returncode != 0:
            raise RuntimeError("git log failed")

        raw_output = b"".join(raw_chunks).decode("utf-8", errors="replace")
        emit("git_log", 0.28, f"Read {commit_count:,} commits from history")

        # --- Stage 3: Parse commits (0.28 – 0.33) ---
        emit("parsing", 0.29, f"Parsing {commit_count:,} commits...")
        parsed_commits = parse_git_log(raw_output)

        if len(parsed_commits) > 50000:
            raise RuntimeError(
                f"Repository has {len(parsed_commits):,} commits "
                f"(limit: 50,000). Try a smaller repo."
            )

        n_changes = sum(len(c["changes"]) for c in parsed_commits)
        emit("parsing", 0.33,
             f"Parsed {len(parsed_commits):,} commits, {n_changes:,} file changes")

        # --- Stage 4: Build base graph (0.33 – 0.38) ---
        emit("building", 0.34, "Building nodes and edges...")
        graph = build_graph(parsed_commits, repo_dir)

        n_files = len(graph["nodes"]["files"])
        n_contributors = len(graph["nodes"]["contributors"])
        n_edges = len(graph["edges"])
        emit("building", 0.38,
             f"Built graph: {n_files:,} files, {n_contributors:,} contributors")

        # --- Stage 5: AI Enrichment (0.38 – 0.92) ---
        has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        if has_api_key:
            from ai_enrichment import (
                generate_concept_map,
                batch_summarize_files,
                batch_tag_files,
                batch_summarize_contributors,
            )
            from concept_extractor import (
                build_concept_hierarchy_edges,
                build_concept_file_edges_from_data,
                enrich_file_summaries_from_data,
                enrich_contributor_summaries_from_data,
            )

            # 5a: Generate concept map (0.38 – 0.44)
            emit("concept_map", 0.39, "Generating concept map with AI...")
            major_concepts, minor_concepts, definitions_str = generate_concept_map(
                repo_dir, f"{owner}/{name}", graph["nodes"]["files"], graph["edges"],
            )
            if major_concepts:
                graph["nodes"]["major_concepts"] = major_concepts
                graph["nodes"]["minor_concepts"] = minor_concepts
                graph["edges"].extend(build_concept_hierarchy_edges(major_concepts, minor_concepts))
                emit("concept_map", 0.44,
                     f"Generated {len(major_concepts)} major, {len(minor_concepts)} minor concepts")
            else:
                emit("concept_map", 0.44, "Concept map generation skipped")

            # 5b: Summarize files (0.44 – 0.64)
            emit("summarizing", 0.45, "Summarizing files with AI...")

            def summary_progress(done, total):
                p = 0.44 + (done / max(total, 1)) * 0.20
                emit("summarizing", min(p, 0.64), f"Summarizing files... {done}/{total}")

            summary_entries = batch_summarize_files(
                repo_dir, graph["nodes"]["files"], graph["edges"],
                progress_cb=summary_progress,
            )
            if summary_entries:
                enrich_file_summaries_from_data(graph["nodes"]["files"], summary_entries)
                emit("summarizing", 0.64, f"Summarized {len(summary_entries)} files")
            else:
                emit("summarizing", 0.64, "File summarization skipped")

            # 5c: Tag files with concepts (0.64 – 0.84)
            if major_concepts and definitions_str:
                emit("tagging", 0.65, "Tagging files with concepts...")

                def tag_progress(done, total):
                    p = 0.64 + (done / max(total, 1)) * 0.20
                    emit("tagging", min(p, 0.84), f"Tagging files... {done}/{total}")

                tag_entries = batch_tag_files(
                    repo_dir, graph["nodes"]["files"], graph["edges"],
                    definitions_str, progress_cb=tag_progress,
                )
                if tag_entries:
                    tagged_edges = build_concept_file_edges_from_data(
                        tag_entries, major_concepts, minor_concepts, graph["nodes"]["files"],
                    )
                    graph["edges"].extend(tagged_edges)
                    emit("tagging", 0.84, f"Tagged {len(tag_entries)} files, {len(tagged_edges)} edges")
                else:
                    emit("tagging", 0.84, "File tagging skipped")
            else:
                emit("tagging", 0.84, "Tagging skipped (no concepts)")

            # 5d: Summarize contributors (0.84 – 0.92)
            emit("contributors", 0.85, "Summarizing contributors...")

            def contrib_progress(done, total):
                p = 0.84 + (done / max(total, 1)) * 0.08
                emit("contributors", min(p, 0.92), f"Summarizing contributors... {done}/{total}")

            contrib_entries = batch_summarize_contributors(
                graph, progress_cb=contrib_progress,
            )
            if contrib_entries:
                enrich_contributor_summaries_from_data(graph["nodes"]["contributors"], contrib_entries)
                emit("contributors", 0.92, f"Summarized {len(contrib_entries)} contributors")
            else:
                emit("contributors", 0.92, "Contributor summaries skipped")
        else:
            emit("building", 0.92, "No API key — skipping AI enrichment")

        # --- Stage 6: Write JSON (0.92 – 1.0) ---
        n_edges = len(graph["edges"])
        emit("writing", 0.93, "Writing graph to disk...")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "knowledge_graph.json")
        with open(output_path, "w") as f:
            json.dump(graph, f)

        emit("done", 1.0,
             f"Done! {n_files:,} files, {n_contributors:,} contributors, "
             f"{n_edges:,} edges, {len(parsed_commits):,} commits")

        return output_path

    except subprocess.TimeoutExpired:
        raise RuntimeError("Build timed out — repository may be too large")
    finally:
        # Clean up clone (keep only the JSON)
        if tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
