import json
import os
import subprocess
import sys
import tempfile

from git_log_parser import parse_git_log
from graph_builder import build_graph

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rust-graph"))
from crate_extractor import extract_crates, build_crate_dependency_edges, map_files_to_crates


def run_git(repo_path, *args, env=None):
    cmd_env = os.environ.copy()
    if env:
        cmd_env.update(env)
    result = subprocess.run(
        ["git"] + list(args),
        cwd=repo_path,
        capture_output=True,
        text=True,
        env=cmd_env,
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result.stdout


def test_integration():
    with tempfile.TemporaryDirectory() as repo_path:
        # Initialize repo
        run_git(repo_path, "init")
        run_git(repo_path, "config", "user.name", "Test")
        run_git(repo_path, "config", "user.email", "test@test.com")

        # Commit 1: Alice adds README.md (3 lines) and src/main.rs (10 lines)
        os.makedirs(os.path.join(repo_path, "src"), exist_ok=True)
        with open(os.path.join(repo_path, "README.md"), "w") as f:
            f.write("line1\nline2\nline3\n")
        with open(os.path.join(repo_path, "src", "main.rs"), "w") as f:
            f.write("\n".join(f"line{i}" for i in range(1, 11)) + "\n")
        run_git(repo_path, "add", "-A")
        run_git(
            repo_path,
            "commit",
            "-m",
            "Add README and main",
            env={
                "GIT_AUTHOR_NAME": "Alice",
                "GIT_AUTHOR_EMAIL": "alice@example.com",
                "GIT_COMMITTER_NAME": "Alice",
                "GIT_COMMITTER_EMAIL": "alice@example.com",
            },
        )

        # Commit 2: Bob modifies src/main.rs, adds src/util.rs
        with open(os.path.join(repo_path, "src", "main.rs"), "a") as f:
            f.write("new line\n")
        with open(os.path.join(repo_path, "src", "util.rs"), "w") as f:
            f.write("util code\n")
        run_git(repo_path, "add", "-A")
        run_git(
            repo_path,
            "commit",
            "-m",
            "Modify main, add util",
            env={
                "GIT_AUTHOR_NAME": "Bob",
                "GIT_AUTHOR_EMAIL": "bob@example.com",
                "GIT_COMMITTER_NAME": "Bob",
                "GIT_COMMITTER_EMAIL": "bob@example.com",
            },
        )

        # Commit 3: Alice renames src/util.rs -> src/helpers.rs
        run_git(repo_path, "mv", "src/util.rs", "src/helpers.rs")
        run_git(repo_path, "add", "-A")
        run_git(
            repo_path,
            "commit",
            "-m",
            "Rename util to helpers",
            env={
                "GIT_AUTHOR_NAME": "Alice",
                "GIT_AUTHOR_EMAIL": "alice@work.com",
                "GIT_COMMITTER_NAME": "Alice",
                "GIT_COMMITTER_EMAIL": "alice@work.com",
            },
        )

        # Commit 4: Alice deletes README.md
        os.remove(os.path.join(repo_path, "README.md"))
        run_git(repo_path, "add", "-A")
        run_git(
            repo_path,
            "commit",
            "-m",
            "Delete README",
            env={
                "GIT_AUTHOR_NAME": "Alice",
                "GIT_AUTHOR_EMAIL": "alice@example.com",
                "GIT_COMMITTER_NAME": "Alice",
                "GIT_COMMITTER_EMAIL": "alice@example.com",
            },
        )

        # Commit 5: Bob modifies src/helpers.rs, adds new README.md
        with open(os.path.join(repo_path, "src", "helpers.rs"), "a") as f:
            f.write("more util code\n")
        with open(os.path.join(repo_path, "README.md"), "w") as f:
            f.write("new readme\ncontent\n")
        run_git(repo_path, "add", "-A")
        run_git(
            repo_path,
            "commit",
            "-m",
            "Modify helpers, add new README",
            env={
                "GIT_AUTHOR_NAME": "Bob",
                "GIT_AUTHOR_EMAIL": "bob@example.com",
                "GIT_COMMITTER_NAME": "Bob",
                "GIT_COMMITTER_EMAIL": "bob@example.com",
            },
        )

        # Run the pipeline
        raw = run_git(
            repo_path,
            "log",
            "--first-parent",
            "--reverse",
            "--format=COMMIT_START%n%H%n%aN%n%aE%n%at%n%s",
            "--name-status",
            "-M",
        )
        parsed = parse_git_log(raw)
        graph = build_graph(parsed, repo_path)

        # Validate JSON serializability
        json_str = json.dumps(graph)
        parsed_back = json.loads(json_str)
        assert parsed_back == graph

        # --- Assertions ---
        files = graph["nodes"]["files"]
        contributors = graph["nodes"]["contributors"]
        edges = graph["edges"]
        commits = graph["commits"]

        # 4 file nodes
        assert len(files) == 4

        # Check specific files
        main_rs = [f for f in files if f["name"] == "src/main.rs"][0]
        helpers_rs = [f for f in files if f["name"] == "src/helpers.rs"][0]
        readmes = [f for f in files if f["name"] == "README.md"]
        deleted_readmes = [f for f in readmes if f["deleted"]]
        live_readmes = [f for f in readmes if not f["deleted"]]

        # src/helpers.rs should have previous_names
        assert helpers_rs["previous_names"] == ["src/util.rs"]
        assert helpers_rs["deleted"] is False
        assert helpers_rs["latest_line_count"] is not None

        # Old README.md should be deleted, new one should be live
        assert len(deleted_readmes) == 1
        assert len(live_readmes) == 1
        assert deleted_readmes[0]["latest_line_count"] is None
        assert live_readmes[0]["latest_line_count"] is not None

        # 2 contributors
        assert len(contributors) == 2
        alice = [c for c in contributors if c["name"] == "Alice"][0]
        bob = [c for c in contributors if c["name"] == "Bob"][0]

        assert alice["total_commits"] == 3
        assert set(alice["emails"]) == {"alice@example.com", "alice@work.com"}
        assert bob["total_commits"] == 2
        assert bob["emails"] == ["bob@example.com"]

        # 5 commits in lookup
        assert len(commits) == 5

        # Edges properly collapsed
        alice_id = alice["id"]
        bob_id = bob["id"]
        main_id = main_rs["id"]

        alice_main_edges = [
            e for e in edges if e["source"] == alice_id and e["target"] == main_id
        ]
        assert len(alice_main_edges) == 1
        assert len(alice_main_edges[0]["commits"]) == 1

        bob_main_edges = [
            e for e in edges if e["source"] == bob_id and e["target"] == main_id
        ]
        assert len(bob_main_edges) == 1
        assert len(bob_main_edges[0]["commits"]) == 1


def test_crate_integration():
    """Create a minimal Cargo workspace with 2 crates, run the full pipeline."""
    with tempfile.TemporaryDirectory() as repo_path:
        # Initialize git repo
        run_git(repo_path, "init")
        run_git(repo_path, "config", "user.name", "Test")
        run_git(repo_path, "config", "user.email", "test@test.com")

        # Create workspace Cargo.toml
        with open(os.path.join(repo_path, "Cargo.toml"), "w") as f:
            f.write('[workspace]\nmembers = ["alpha", "beta"]\n')

        # Create crate alpha (lib, depends on beta)
        alpha_dir = os.path.join(repo_path, "alpha")
        os.makedirs(os.path.join(alpha_dir, "src"))
        with open(os.path.join(alpha_dir, "Cargo.toml"), "w") as f:
            f.write(
                '[package]\nname = "alpha"\nversion = "0.1.0"\nedition = "2021"\n\n'
                '[dependencies]\nbeta = { path = "../beta" }\n'
            )
        with open(os.path.join(alpha_dir, "src", "lib.rs"), "w") as f:
            f.write("pub fn alpha_fn() {}\n")

        # Create crate beta (lib, no workspace deps)
        beta_dir = os.path.join(repo_path, "beta")
        os.makedirs(os.path.join(beta_dir, "src"))
        with open(os.path.join(beta_dir, "Cargo.toml"), "w") as f:
            f.write(
                '[package]\nname = "beta"\nversion = "0.1.0"\nedition = "2021"\n'
            )
        with open(os.path.join(beta_dir, "src", "lib.rs"), "w") as f:
            f.write("pub fn beta_fn() {}\n")

        # Commit everything
        run_git(repo_path, "add", "-A")
        run_git(
            repo_path,
            "commit",
            "-m",
            "Initial workspace",
            env={
                "GIT_AUTHOR_NAME": "Alice",
                "GIT_AUTHOR_EMAIL": "alice@example.com",
                "GIT_COMMITTER_NAME": "Alice",
                "GIT_COMMITTER_EMAIL": "alice@example.com",
            },
        )

        # Run git pipeline
        raw = run_git(
            repo_path,
            "log",
            "--first-parent",
            "--reverse",
            "--format=COMMIT_START%n%H%n%aN%n%aE%n%at%n%s",
            "--name-status",
            "-M",
        )
        parsed = parse_git_log(raw)
        graph = build_graph(parsed, repo_path)

        # Run crate extraction
        crate_nodes = extract_crates(repo_path)
        dep_edges = build_crate_dependency_edges(repo_path, crate_nodes)
        contains_edges = map_files_to_crates(graph["nodes"]["files"], crate_nodes)

        graph["nodes"]["crates"] = crate_nodes
        graph["edges"].extend(dep_edges)
        graph["edges"].extend(contains_edges)

        # --- Assertions ---

        # 2 crate nodes
        assert len(graph["nodes"]["crates"]) == 2
        crate_names = {c["name"] for c in graph["nodes"]["crates"]}
        assert crate_names == {"alpha", "beta"}

        # 1 depends_on edge (alpha -> beta)
        dep_edges_in_graph = [e for e in graph["edges"] if e.get("type") == "depends_on"]
        assert len(dep_edges_in_graph) == 1
        alpha_crate = [c for c in crate_nodes if c["name"] == "alpha"][0]
        beta_crate = [c for c in crate_nodes if c["name"] == "beta"][0]
        assert dep_edges_in_graph[0]["source"] == alpha_crate["id"]
        assert dep_edges_in_graph[0]["target"] == beta_crate["id"]

        # Each .rs file has a contains edge linking to correct crate
        contains_in_graph = [e for e in graph["edges"] if e.get("type") == "contains"]
        rs_files = [f for f in graph["nodes"]["files"] if f["name"].endswith(".rs")]
        assert len(rs_files) >= 2
        for rs_file in rs_files:
            matching = [e for e in contains_in_graph if e["target"] == rs_file["id"]]
            assert len(matching) == 1
            # Check correct crate assignment
            if rs_file["name"].startswith("alpha/"):
                assert matching[0]["source"] == alpha_crate["id"]
            elif rs_file["name"].startswith("beta/"):
                assert matching[0]["source"] == beta_crate["id"]

        # All edges have a type field
        for edge in graph["edges"]:
            assert "type" in edge
            assert edge["type"] in ("authored", "depends_on", "contains")

        # JSON round-trip
        json_str = json.dumps(graph)
        parsed_back = json.loads(json_str)
        assert parsed_back == graph
