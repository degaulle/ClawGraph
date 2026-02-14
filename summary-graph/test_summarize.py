#!/usr/bin/env python3
"""Unit and integration tests for summarize.py and batch_summarize.py.

All API calls are mocked — no real requests are made.
Integration tests use codex-rs/apply-patch/src/ as a real file set.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from summarize import build_prompt, load_template, load_source, summarize_content, summarize_file
from batch_summarize import discover_files, result_filename, process_one

CODEX_ROOT = Path(__file__).resolve().parent.parent.parent / "codex"
APPLY_PATCH_SRC = CODEX_ROOT / "codex-rs" / "apply-patch" / "src"
TEMPLATE_PATH = Path(__file__).resolve().parent / "template" / "summarize_file.template"

MOCK_RESPONSE_TEXT = "This file does something interesting."


def _mock_anthropic_client():
    """Create a mock Anthropic client that returns a canned response."""
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text=MOCK_RESPONSE_TEXT)]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_message
    return mock_client


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def test_substitutes_content(self):
        template = "Summarize:\n```\n%FILE_CONTENT%\n```"
        result = build_prompt(template, {"%FILE_CONTENT%": "fn main() {}"})
        assert "fn main() {}" in result
        assert "%FILE_CONTENT%" not in result

    def test_substitutes_path(self):
        template = "Path: %FILE_PATH%\n```\n%FILE_CONTENT%\n```"
        result = build_prompt(template, {"%FILE_CONTENT%": "code", "%FILE_PATH%": "src/main.rs"})
        assert "src/main.rs" in result
        assert "%FILE_PATH%" not in result

    def test_missing_content_token_raises(self):
        with pytest.raises(ValueError, match="FILE_CONTENT"):
            build_prompt("no token here", {"%FILE_CONTENT%": "code"})

    def test_empty_path_default(self):
        template = "Path: %FILE_PATH%\n%FILE_CONTENT%"
        result = build_prompt(template, {"%FILE_CONTENT%": "code", "%FILE_PATH%": ""})
        assert "Path: \n" in result

    def test_custom_token_substitution(self):
        template = "Defs: %CONCEPT_DEFINITIONS%\n%FILE_CONTENT%"
        result = build_prompt(template, {
            "%FILE_CONTENT%": "code",
            "%CONCEPT_DEFINITIONS%": "concept A, concept B",
        })
        assert "concept A, concept B" in result
        assert "%CONCEPT_DEFINITIONS%" not in result

    def test_file_content_substituted_last(self):
        """Tokens inside FILE_CONTENT must not be treated as template tokens."""
        template = "Defs: %CUSTOM%\n%FILE_CONTENT%"
        result = build_prompt(template, {
            "%FILE_CONTENT%": "source with %CUSTOM% literal",
            "%CUSTOM%": "replaced",
        })
        assert "Defs: replaced" in result
        # The %CUSTOM% inside the source content should survive as-is
        assert "source with %CUSTOM% literal" in result


class TestLoadFiles:
    def test_load_template(self):
        content = load_template(str(TEMPLATE_PATH))
        assert "%FILE_CONTENT%" in content
        assert "%FILE_PATH%" in content

    def test_load_source_reads_real_file(self):
        rs_file = APPLY_PATCH_SRC / "lib.rs"
        if rs_file.exists():
            content = load_source(str(rs_file))
            assert len(content) > 0


class TestDiscoverFiles:
    def test_finds_rs_files(self):
        files = discover_files(str(APPLY_PATCH_SRC), "*.rs")
        assert len(files) == 6
        assert all(f.suffix == ".rs" for f in files)

    def test_sorted_output(self):
        files = discover_files(str(APPLY_PATCH_SRC), "*.rs")
        names = [f.name for f in files]
        assert names == sorted(names)

    def test_no_matches(self):
        files = discover_files(str(APPLY_PATCH_SRC), "*.py")
        assert files == []


class TestResultFilename:
    def test_flattens_path(self):
        source = Path("/repo/codex-rs/apply-patch/src/parser.rs")
        name = result_filename(source, "/repo")
        assert name == "codex-rs__apply-patch__src__parser.rs.json"

    def test_single_level(self):
        source = Path("/repo/main.rs")
        name = result_filename(source, "/repo")
        assert name == "main.rs.json"


# ---------------------------------------------------------------------------
# Integration tests (mocked API, real files)
# ---------------------------------------------------------------------------

class TestSummarizeContent:
    @patch("summarize.anthropic.Anthropic")
    def test_returns_expected_keys(self, mock_cls):
        mock_cls.return_value = _mock_anthropic_client()
        result = summarize_content(
            "Summarize:\n%FILE_CONTENT%",
            {"%FILE_CONTENT%": "fn main() {}", "%FILE_PATH%": "main.rs"},
        )
        assert result["response"] == MOCK_RESPONSE_TEXT
        assert result["source_length"] == len("fn main() {}")
        assert "model" in result

    @patch("summarize.anthropic.Anthropic")
    def test_passes_prompt_to_api(self, mock_cls):
        client = _mock_anthropic_client()
        mock_cls.return_value = client
        summarize_content(
            "Review:\n%FILE_CONTENT%",
            {"%FILE_CONTENT%": "let x = 1;", "%FILE_PATH%": "test.rs"},
        )
        call_args = client.messages.create.call_args
        prompt = call_args.kwargs["messages"][0]["content"]
        assert "let x = 1;" in prompt

    @patch("summarize.anthropic.Anthropic")
    def test_json_schema_passed_to_api(self, mock_cls):
        client = _mock_anthropic_client()
        mock_cls.return_value = client
        schema = {"type": "object", "properties": {"tags": {"type": "array"}}}
        summarize_content(
            "Tag:\n%FILE_CONTENT%",
            {"%FILE_CONTENT%": "code"},
            json_schema=schema,
        )
        call_args = client.messages.create.call_args
        assert "output_config" in call_args.kwargs
        assert call_args.kwargs["output_config"]["format"]["type"] == "json_schema"
        assert call_args.kwargs["output_config"]["format"]["schema"] is schema

    @patch("summarize.anthropic.Anthropic")
    def test_json_schema_omitted_when_none(self, mock_cls):
        client = _mock_anthropic_client()
        mock_cls.return_value = client
        summarize_content(
            "Summarize:\n%FILE_CONTENT%",
            {"%FILE_CONTENT%": "code"},
        )
        call_args = client.messages.create.call_args
        assert "output_config" not in call_args.kwargs


class TestSummarizeFile:
    @patch("summarize.anthropic.Anthropic")
    def test_summarizes_real_file(self, mock_cls):
        mock_cls.return_value = _mock_anthropic_client()
        rs_file = APPLY_PATCH_SRC / "lib.rs"
        if not rs_file.exists():
            pytest.skip("codex repo not available")
        result = summarize_file(str(TEMPLATE_PATH), str(rs_file))
        assert result["response"] == MOCK_RESPONSE_TEXT
        assert result["source_path"] == str(rs_file)
        assert result["source_length"] > 0


class TestProcessOne:
    @patch("summarize.anthropic.Anthropic")
    def test_writes_json_file(self, mock_cls):
        mock_cls.return_value = _mock_anthropic_client()
        rs_file = APPLY_PATCH_SRC / "lib.rs"
        if not rs_file.exists():
            pytest.skip("codex repo not available")
        template = load_template(str(TEMPLATE_PATH))
        with tempfile.TemporaryDirectory() as run_dir:
            status = process_one(
                template, rs_file, str(APPLY_PATCH_SRC),
                Path(run_dir), "claude-haiku-4-5-20251001", 4096,
            )
            assert status["status"] == "ok"
            out_path = Path(status["output"])
            assert out_path.exists()
            data = json.loads(out_path.read_text())
            assert data["response"] == MOCK_RESPONSE_TEXT
            assert data["source_path"] == "lib.rs"

    @patch("summarize.anthropic.Anthropic")
    def test_batch_all_apply_patch_files(self, mock_cls):
        """Integration: process all 6 apply-patch/src/*.rs files."""
        mock_cls.return_value = _mock_anthropic_client()
        files = discover_files(str(APPLY_PATCH_SRC), "*.rs")
        if not files:
            pytest.skip("codex repo not available")
        template = load_template(str(TEMPLATE_PATH))
        with tempfile.TemporaryDirectory() as run_dir:
            results = []
            for f in files:
                status = process_one(
                    template, f, str(APPLY_PATCH_SRC),
                    Path(run_dir), "claude-haiku-4-5-20251001", 4096,
                )
                results.append(status)
            assert all(r["status"] == "ok" for r in results)
            json_files = list(Path(run_dir).glob("*.json"))
            assert len(json_files) == 6

    @patch("summarize.anthropic.Anthropic")
    def test_process_one_with_extra_substitutions(self, mock_cls):
        """Extra substitutions are forwarded to the API call."""
        client = _mock_anthropic_client()
        mock_cls.return_value = client
        template = "Defs: %CONCEPT_DEFINITIONS%\nPath: %FILE_PATH%\n%FILE_CONTENT%"
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "test.rs"
            src.write_text("fn main() {}")
            status = process_one(
                template, src, tmp,
                Path(tmp), "claude-haiku-4-5-20251001", 4096,
                extra_substitutions={"%CONCEPT_DEFINITIONS%": "concept A"},
            )
            assert status["status"] == "ok"
            call_args = client.messages.create.call_args
            prompt = call_args.kwargs["messages"][0]["content"]
            assert "concept A" in prompt
            assert "%CONCEPT_DEFINITIONS%" not in prompt
