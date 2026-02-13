from git_log_parser import parse_git_log


def test_single_commit_one_modified_file():
    raw = (
        "COMMIT_START\n"
        "abc123def456\n"
        "John Doe\n"
        "john@example.com\n"
        "1700000000\n"
        "Initial commit\n"
        "\n"
        "M\tsrc/main.rs\n"
    )
    result = parse_git_log(raw)
    assert len(result) == 1
    commit = result[0]
    assert commit["hash"] == "abc123def456"
    assert commit["author"] == "John Doe"
    assert commit["email"] == "john@example.com"
    assert commit["timestamp"] == 1700000000
    assert commit["message"] == "Initial commit"
    assert len(commit["changes"]) == 1
    assert commit["changes"][0] == {"status": "M", "path": "src/main.rs"}


def test_commit_with_all_change_types():
    raw = (
        "COMMIT_START\n"
        "abc123\n"
        "John Doe\n"
        "john@example.com\n"
        "1700000000\n"
        "Various changes\n"
        "\n"
        "A\tnew_file.rs\n"
        "M\tmodified_file.rs\n"
        "D\tdeleted_file.rs\n"
        "R100\told/path.rs\tnew/path.rs\n"
    )
    result = parse_git_log(raw)
    assert len(result) == 1
    changes = result[0]["changes"]
    assert len(changes) == 4
    assert changes[0] == {"status": "A", "path": "new_file.rs"}
    assert changes[1] == {"status": "M", "path": "modified_file.rs"}
    assert changes[2] == {"status": "D", "path": "deleted_file.rs"}
    assert changes[3] == {
        "status": "R",
        "old_path": "old/path.rs",
        "new_path": "new/path.rs",
        "score": 100,
    }


def test_multiple_commits():
    raw = (
        "COMMIT_START\n"
        "hash1\n"
        "Alice\n"
        "alice@example.com\n"
        "1700000000\n"
        "First commit\n"
        "\n"
        "A\tfile1.rs\n"
        "COMMIT_START\n"
        "hash2\n"
        "Bob\n"
        "bob@example.com\n"
        "1700000001\n"
        "Second commit\n"
        "\n"
        "M\tfile1.rs\n"
    )
    result = parse_git_log(raw)
    assert len(result) == 2
    assert result[0]["hash"] == "hash1"
    assert result[0]["author"] == "Alice"
    assert result[1]["hash"] == "hash2"
    assert result[1]["author"] == "Bob"


def test_commit_with_no_file_changes():
    raw = (
        "COMMIT_START\n"
        "hash1\n"
        "Alice\n"
        "alice@example.com\n"
        "1700000000\n"
        "Empty commit\n"
    )
    result = parse_git_log(raw)
    assert len(result) == 1
    assert result[0]["changes"] == []


def test_paths_with_spaces():
    raw = (
        "COMMIT_START\n"
        "hash1\n"
        "Alice\n"
        "alice@example.com\n"
        "1700000000\n"
        "Spaces in path\n"
        "\n"
        'M\t"path with spaces/file.rs"\n'
    )
    result = parse_git_log(raw)
    assert result[0]["changes"][0]["path"] == "path with spaces/file.rs"


def test_various_rename_scores():
    raw = (
        "COMMIT_START\n"
        "hash1\n"
        "Alice\n"
        "alice@example.com\n"
        "1700000000\n"
        "Renames\n"
        "\n"
        "R075\told1.rs\tnew1.rs\n"
        "R100\told2.rs\tnew2.rs\n"
    )
    result = parse_git_log(raw)
    changes = result[0]["changes"]
    assert changes[0]["score"] == 75
    assert changes[1]["score"] == 100
