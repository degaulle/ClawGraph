import tempfile
from datetime import datetime, timezone

from graph_builder import build_graph


def test_single_commit_single_file():
    commits = [
        {
            "hash": "aaa",
            "author": "Alice",
            "email": "alice@example.com",
            "timestamp": 1700000000,
            "message": "Add file",
            "changes": [{"status": "A", "path": "foo.rs"}],
        }
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        result = build_graph(commits, tmpdir)
    assert len(result["nodes"]["files"]) == 1
    assert len(result["nodes"]["contributors"]) == 1
    assert len(result["edges"]) == 1
    assert len(result["edges"][0]["commits"]) == 1


def test_edge_collapsing():
    commits = [
        {
            "hash": "aaa",
            "author": "Alice",
            "email": "alice@example.com",
            "timestamp": 1700000000,
            "message": "Add file",
            "changes": [{"status": "A", "path": "foo.rs"}],
        },
        {
            "hash": "bbb",
            "author": "Alice",
            "email": "alice@example.com",
            "timestamp": 1700000001,
            "message": "Modify file",
            "changes": [{"status": "M", "path": "foo.rs"}],
        },
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        result = build_graph(commits, tmpdir)
    assert len(result["edges"]) == 1
    assert len(result["edges"][0]["commits"]) == 2
    assert result["edges"][0]["commits"] == ["aaa", "bbb"]


def test_multiple_contributors():
    commits = [
        {
            "hash": "aaa",
            "author": "Alice",
            "email": "alice@example.com",
            "timestamp": 1700000000,
            "message": "Add file",
            "changes": [{"status": "A", "path": "foo.rs"}],
        },
        {
            "hash": "bbb",
            "author": "Bob",
            "email": "bob@example.com",
            "timestamp": 1700000001,
            "message": "Modify file",
            "changes": [{"status": "M", "path": "foo.rs"}],
        },
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        result = build_graph(commits, tmpdir)
    assert len(result["nodes"]["contributors"]) == 2
    assert len(result["edges"]) == 2
    # Both edges point to the same file
    file_ids = {e["target"] for e in result["edges"]}
    assert len(file_ids) == 1


def test_contributor_metadata():
    commits = [
        {
            "hash": "aaa",
            "author": "Alice",
            "email": "alice@example.com",
            "timestamp": 1700000000,
            "message": "Commit 1",
            "changes": [{"status": "A", "path": "foo.rs"}],
        },
        {
            "hash": "bbb",
            "author": "Alice",
            "email": "alice@work.com",
            "timestamp": 1700000001,
            "message": "Commit 2",
            "changes": [{"status": "M", "path": "foo.rs"}],
        },
        {
            "hash": "ccc",
            "author": "Alice",
            "email": "alice@example.com",
            "timestamp": 1700000002,
            "message": "Commit 3",
            "changes": [{"status": "M", "path": "foo.rs"}],
        },
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        result = build_graph(commits, tmpdir)
    contributors = result["nodes"]["contributors"]
    assert len(contributors) == 1
    alice = contributors[0]
    assert alice["total_commits"] == 3
    assert set(alice["emails"]) == {"alice@example.com", "alice@work.com"}
    assert alice["first_commit_at"] == datetime.fromtimestamp(
        1700000000, tz=timezone.utc
    ).isoformat()


def test_commit_lookup():
    commits = [
        {
            "hash": "aaa",
            "author": "Alice",
            "email": "alice@example.com",
            "timestamp": 1700000000,
            "message": "First",
            "changes": [{"status": "A", "path": "foo.rs"}],
        },
        {
            "hash": "bbb",
            "author": "Bob",
            "email": "bob@example.com",
            "timestamp": 1700000001,
            "message": "Second",
            "changes": [{"status": "M", "path": "foo.rs"}],
        },
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        result = build_graph(commits, tmpdir)
    assert len(result["commits"]) == 2
    assert "aaa" in result["commits"]
    assert "bbb" in result["commits"]
    assert result["commits"]["aaa"]["message"] == "First"
    assert result["commits"]["bbb"]["message"] == "Second"
    # Author should be contributor ID, not name
    assert result["commits"]["aaa"]["author"].startswith("contributor_")


def test_existing_edges_have_authored_type():
    commits = [
        {
            "hash": "aaa",
            "author": "Alice",
            "email": "alice@example.com",
            "timestamp": 1700000000,
            "message": "Add file",
            "changes": [{"status": "A", "path": "foo.rs"}],
        },
        {
            "hash": "bbb",
            "author": "Bob",
            "email": "bob@example.com",
            "timestamp": 1700000001,
            "message": "Modify file",
            "changes": [{"status": "M", "path": "foo.rs"}],
        },
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        result = build_graph(commits, tmpdir)
    for edge in result["edges"]:
        assert "type" in edge
        assert edge["type"] == "authored"


def test_file_timestamps():
    commits = [
        {
            "hash": "aaa",
            "author": "Alice",
            "email": "alice@example.com",
            "timestamp": 1000,
            "message": "Add file",
            "changes": [{"status": "A", "path": "foo.rs"}],
        },
        {
            "hash": "bbb",
            "author": "Alice",
            "email": "alice@example.com",
            "timestamp": 2000,
            "message": "Other change",
            "changes": [{"status": "A", "path": "bar.rs"}],
        },
        {
            "hash": "ccc",
            "author": "Alice",
            "email": "alice@example.com",
            "timestamp": 3000,
            "message": "Modify",
            "changes": [{"status": "M", "path": "foo.rs"}],
        },
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        result = build_graph(commits, tmpdir)
    files = result["nodes"]["files"]
    foo = [f for f in files if f["name"] == "foo.rs"][0]
    assert foo["created_at"] == datetime.fromtimestamp(
        1000, tz=timezone.utc
    ).isoformat()
    assert foo["last_modified_at"] == datetime.fromtimestamp(
        3000, tz=timezone.utc
    ).isoformat()
