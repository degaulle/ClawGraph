#!/usr/bin/env python3
"""Source code summarizer using the Anthropic API.

Usage as CLI:
    python summarize.py --template prompt.txt --source main.rs
    python summarize.py --template prompt.txt --source main.rs --output result.json

Usage as library:
    from summarize import summarize_file, summarize_content
    result = summarize_file("prompt.txt", "main.rs")
    result = summarize_content(template_str, source_str)
"""

import argparse
import json
import logging
import os
import sys
import time

import anthropic

log = logging.getLogger(__name__)

REQUIRED_TOKEN = "%FILE_CONTENT%"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def load_template(template_path: str) -> str:
    with open(template_path, "r") as f:
        return f.read()


def load_source(source_path: str) -> str:
    with open(source_path, "r") as f:
        return f.read()


def build_prompt(template: str, substitutions: dict[str, str]) -> str:
    if REQUIRED_TOKEN not in template:
        raise ValueError(f"Template must contain the {REQUIRED_TOKEN} token")
    prompt = template
    file_content = substitutions.get(REQUIRED_TOKEN, "")
    for token, value in substitutions.items():
        if token == REQUIRED_TOKEN:
            continue
        prompt = prompt.replace(token, value)
    prompt = prompt.replace(REQUIRED_TOKEN, file_content)
    return prompt


def summarize_content(
    template: str,
    substitutions: dict[str, str],
    model: str = DEFAULT_MODEL,
    max_tokens: int = 4096,
    json_schema: dict | None = None,
    *,
    max_retries: int = 5,
    retry_base_delay: float = 2.0,
) -> dict:
    """Summarize source content using a prompt template.

    Args:
        template: Prompt template string containing %FILE_CONTENT%.
        substitutions: Token-to-value mapping (must include %FILE_CONTENT%).
        model: Anthropic model to use.
        max_tokens: Max tokens in the response.
        json_schema: Optional JSON schema for structured output.
        max_retries: Max number of retries on rate-limit errors.
        retry_base_delay: Base delay in seconds for exponential backoff.

    Returns:
        Dict with keys: model, source_length, response.
    """
    prompt = build_prompt(template, substitutions)
    source_content = substitutions.get(REQUIRED_TOKEN, "")
    file_path = substitutions.get("%FILE_PATH%", "<inline>")
    log.debug("Prompt length: %d chars for %s", len(prompt), file_path)
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    log.info("Calling %s for %s (%d chars)", model, file_path, len(source_content))
    api_kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if json_schema is not None:
        api_kwargs["output_config"] = {
            "format": {"type": "json_schema", "schema": json_schema},
        }
    for attempt in range(max_retries + 1):
        try:
            message = client.messages.create(**api_kwargs)
            break
        except anthropic.RateLimitError:
            if attempt == max_retries:
                raise
            backoff = retry_base_delay * (2 ** attempt)
            retry_after_hdr = sys.exc_info()[1].response.headers.get("retry-after")
            if retry_after_hdr is not None:
                backoff = max(backoff, float(retry_after_hdr))
            log.warning(
                "Rate-limited on %s (attempt %d/%d), sleeping %.1fs",
                file_path, attempt + 1, max_retries, backoff,
            )
            time.sleep(backoff)
    response_text = message.content[0].text
    log.info("Got response for %s (%d chars)", file_path, len(response_text))
    return {
        "model": model,
        "source_length": len(source_content),
        "response": response_text,
    }


def summarize_file(
    template_path: str,
    source_path: str,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 4096,
    extra_substitutions: dict[str, str] | None = None,
    json_schema: dict | None = None,
) -> dict:
    """Summarize a source file using a prompt template file.

    Returns:
        Dict with keys: model, source_path, source_length, response.
    """
    template = load_template(template_path)
    source_content = load_source(source_path)
    substitutions = {REQUIRED_TOKEN: source_content, "%FILE_PATH%": source_path}
    if extra_substitutions:
        substitutions.update(extra_substitutions)
    result = summarize_content(template, substitutions, model, max_tokens, json_schema)
    result["source_path"] = source_path
    return result


def main():
    parser = argparse.ArgumentParser(description="Summarize source code via the Anthropic API.")
    parser.add_argument("--template", required=True, help="Path to prompt template containing %%FILE_CONTENT%%")
    parser.add_argument("--source", required=True, help="Path to source file")
    parser.add_argument("--output", help="Path to write JSON output (default: stdout)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Model to use (default: {DEFAULT_MODEL})")
    parser.add_argument("--max-tokens", type=int, default=4096, help="Max response tokens (default: 4096)")
    parser.add_argument("--definitions", help="Path to file whose contents replace %%CONCEPT_DEFINITIONS%%")
    parser.add_argument("--json-schema", help="Path to JSON schema file for structured output")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.error("ANTHROPIC_API_KEY environment variable is not set.")
        sys.exit(1)

    extra_substitutions = {}
    if args.definitions:
        extra_substitutions["%CONCEPT_DEFINITIONS%"] = load_source(args.definitions)

    schema = None
    if args.json_schema:
        with open(args.json_schema, "r") as f:
            schema = json.load(f)

    log.info("Summarizing %s with template %s", args.source, args.template)
    result = summarize_file(
        args.template, args.source, args.model, args.max_tokens,
        extra_substitutions=extra_substitutions or None,
        json_schema=schema,
    )
    output = json.dumps(result, indent=2)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output + "\n")
        log.info("Result written to %s", args.output)
    else:
        print(output)


if __name__ == "__main__":
    main()
