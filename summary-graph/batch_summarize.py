#!/usr/bin/env python3
"""Batch source code summarizer using a thread pool.

Discovers files via glob pattern under a root directory, summarizes each
using the Anthropic API, and writes individual JSON results into a
timestamped run folder under output/.

Usage:
    python batch_summarize.py --template summarize.template --root ../../codex --glob '**/*.rs'
    python batch_summarize.py --template summarize.template --root ../../codex --glob '**/*.rs' --workers 32
"""

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

from summarize import summarize_content, load_template, load_source, DEFAULT_MODEL, REQUIRED_TOKEN

# Rate limit: 4K req/min → ~267 max threads at ~4s avg latency. Use half.
DEFAULT_WORKERS = 64
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "output"


def discover_files(root: str, pattern: str) -> list[Path]:
    root_path = Path(root)
    return sorted(root_path.glob(pattern))


def make_run_dir() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = OUTPUT_DIR / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def result_filename(source_path: Path, root: str) -> str:
    """Derive a flat JSON filename from the relative source path."""
    rel = source_path.relative_to(root)
    return str(rel).replace("/", "__") + ".json"


def process_one(
    template: str,
    source_path: Path,
    root: str,
    run_dir: Path,
    model: str,
    max_tokens: int,
    extra_substitutions: dict[str, str] | None = None,
    json_schema: dict | None = None,
) -> dict:
    """Summarize one file and write its JSON result. Returns a status dict."""
    rel_path = str(source_path.relative_to(root))
    try:
        source_content = load_source(str(source_path))
        substitutions = {REQUIRED_TOKEN: source_content, "%FILE_PATH%": rel_path}
        if extra_substitutions:
            substitutions.update(extra_substitutions)
        result = summarize_content(template, substitutions, model, max_tokens, json_schema)
        result["source_path"] = rel_path

        out_file = run_dir / result_filename(source_path, root)
        with open(out_file, "w") as f:
            json.dump(result, f, indent=2)
            f.write("\n")

        log.debug("Wrote %s", out_file)
        return {"path": rel_path, "status": "ok", "output": str(out_file)}
    except Exception as e:
        log.warning("Failed %s: %s", rel_path, e)
        return {"path": rel_path, "status": "error", "error": str(e)}


def collect_run(run_dir: Path) -> list[dict]:
    """Load all individual JSON results from a run directory."""
    results = []
    for json_file in sorted(run_dir.glob("*.json")):
        with open(json_file, "r") as f:
            results.append(json.load(f))
    return results


def collect(run_dir_name: str, prefix: str = "summary") -> Path:
    """Collect all results from a run folder into a single summary JSON.

    Args:
        run_dir_name: Name of the timestamped run folder inside output/.
        prefix: Filename prefix for the output (e.g. "summarize_file", "tag_file").

    Returns:
        Path to the generated summary file.
    """
    run_dir = OUTPUT_DIR / run_dir_name
    if not run_dir.is_dir():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")

    results = collect_run(run_dir)
    summary = {
        "run": run_dir_name,
        "file_count": len(results),
        "files": results,
    }

    out_path = OUTPUT_DIR / f"{prefix}_{run_dir_name}.json"
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)
        f.write("\n")

    log.info("Collected %d results into %s", len(results), out_path)
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Batch-summarize source files via the Anthropic API.")
    subparsers = parser.add_subparsers(dest="command")

    # Default: run batch summarization
    run_parser = subparsers.add_parser("run", help="Run batch summarization")
    run_parser.add_argument("--template", required=True, help="Path to prompt template")
    run_parser.add_argument("--root", required=True, help="Root directory to search")
    run_parser.add_argument("--glob", required=True, help="Glob pattern for file discovery (e.g. '**/*.rs')")
    run_parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"Thread pool size (default: {DEFAULT_WORKERS})")
    run_parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Model to use (default: {DEFAULT_MODEL})")
    run_parser.add_argument("--max-tokens", type=int, default=4096, help="Max response tokens (default: 4096)")
    run_parser.add_argument("--no-collect", action="store_true", help="Skip auto-collecting results into a summary JSON")
    run_parser.add_argument("--definitions", help="Path to file whose contents replace %%CONCEPT_DEFINITIONS%%")
    run_parser.add_argument("--json-schema", help="Path to JSON schema file for structured output")

    # Collect: gather results into a single JSON
    collect_parser = subparsers.add_parser("collect", help="Collect a run folder into a single summary JSON")
    collect_parser.add_argument("run_dir", help="Name of the timestamped run folder (e.g. 20260214_222035)")
    collect_parser.add_argument("--prefix", default="summary", help="Filename prefix for collected output (default: summary)")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.command == "collect":
        collect(args.run_dir, prefix=args.prefix)
        return

    # command == "run"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.error("ANTHROPIC_API_KEY environment variable is not set.")
        sys.exit(1)

    files = discover_files(args.root, args.glob)
    if not files:
        log.error("No files matched '%s' under %s", args.glob, args.root)
        sys.exit(1)

    template = load_template(args.template)
    prefix = Path(args.template).stem  # e.g. "summarize_file", "tag_file"

    extra_substitutions = {}
    if args.definitions:
        extra_substitutions["%CONCEPT_DEFINITIONS%"] = load_source(args.definitions)

    schema = None
    if args.json_schema:
        with open(args.json_schema, "r") as f:
            schema = json.load(f)

    run_dir = make_run_dir()
    log.info("Found %d files. Writing results to %s/", len(files), run_dir)
    log.info("Workers: %d, Model: %s", args.workers, args.model)

    t0 = time.time()
    ok, errors = 0, 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                process_one, template, f, args.root, run_dir, args.model, args.max_tokens,
                extra_substitutions=extra_substitutions or None,
                json_schema=schema,
            ): f
            for f in files
        }
        for future in as_completed(futures):
            result = future.result()
            if result["status"] == "ok":
                ok += 1
                log.info("[%d/%d] OK  %s", ok + errors, len(files), result["path"])
            else:
                errors += 1
                log.error("[%d/%d] ERR %s: %s", ok + errors, len(files), result["path"], result["error"])

    elapsed = time.time() - t0
    log.info("Done in %.1fs — %d succeeded, %d failed, %d total", elapsed, ok, errors, len(files))

    if not args.no_collect:
        collect(run_dir.name, prefix=prefix)


if __name__ == "__main__":
    main()
