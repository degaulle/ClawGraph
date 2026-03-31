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
        # --- Stage 1: Clone ---
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

        # Git progress uses \r for in-place updates. Read raw bytes in chunks.
        buf = b""
        while True:
            chunk = proc.stderr.read(256)
            if not chunk:
                break
            buf += chunk
            # Process any complete lines (split on \r or \n)
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
                m = re.search(r'(\d+)%', line)
                if m:
                    pct = int(m.group(1)) / 100.0
                    emit("cloning", pct * 0.4, f"Cloning {owner}/{name}... {m.group(1)}%")

        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"git clone failed (exit {proc.returncode})")

        emit("cloning", 0.4, f"Clone complete")

        # --- Stage 2: Parse git log ---
        emit("parsing", 0.45, "Parsing git history...")
        result = subprocess.run(
            [
                "git", "log", "--first-parent", "--reverse",
                "--format=COMMIT_START%n%H%n%aN%n%aE%n%at%n%s",
                "--name-status", "-M",
            ],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git log failed: {result.stderr[:200]}")

        parsed_commits = parse_git_log(result.stdout)

        # Safety: reject extremely large repos
        if len(parsed_commits) > 50000:
            raise RuntimeError(
                f"Repository has {len(parsed_commits):,} commits "
                f"(limit: 50,000). Try a smaller repo."
            )

        emit("parsing", 0.55, f"Parsed {len(parsed_commits):,} commits")

        # --- Stage 3: Build graph ---
        emit("building", 0.6, "Building knowledge graph...")
        graph = build_graph(parsed_commits, repo_dir)

        n_files = len(graph["nodes"]["files"])
        n_contributors = len(graph["nodes"]["contributors"])
        n_edges = len(graph["edges"])
        emit("building", 0.85, f"{n_files:,} files, {n_contributors:,} contributors")

        # --- Stage 4: Write JSON ---
        emit("writing", 0.9, "Writing graph...")
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
