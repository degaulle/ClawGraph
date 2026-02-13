import json
import os
import subprocess
import tempfile

from git_log_parser import parse_git_log
from graph_builder import build_graph


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
