import os
import tempfile

from file_metadata import get_file_type, get_line_count


def test_file_type_rs():
    assert get_file_type("foo.rs") == "rs"


def test_file_type_no_extension():
    assert get_file_type("Makefile") is None


def test_file_type_double_extension():
    assert get_file_type("foo.tar.gz") == "gz"


def test_file_type_hidden_file():
    assert get_file_type(".gitignore") is None


def test_line_count_exists():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("line1\nline2\nline3\nline4\nline5\n")
        path = f.name
    try:
        assert get_line_count(path) == 5
    finally:
        os.unlink(path)


def test_line_count_missing():
    assert get_line_count("/nonexistent/path/to/file.txt") is None


def test_line_count_empty():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        path = f.name
    try:
        assert get_line_count(path) == 0
    finally:
        os.unlink(path)
