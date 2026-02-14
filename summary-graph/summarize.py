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

import anthropic

log = logging.getLogger(__name__)

TOKEN_CONTENT = "%FILE_CONTENT%"
TOKEN_PATH = "%FILE_PATH%"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def load_template(template_path: str) -> str:
    with open(template_path, "r") as f:
        return f.read()


def load_source(source_path: str) -> str:
    with open(source_path, "r") as f:
        return f.read()


def build_prompt(template: str, source_content: str, file_path: str = "") -> str:
    if TOKEN_CONTENT not in template:
        raise ValueError(f"Template must contain the {TOKEN_CONTENT} token")
    prompt = template.replace(TOKEN_CONTENT, source_content)
    prompt = prompt.replace(TOKEN_PATH, file_path)
    return prompt


def summarize_content(
    template: str,
    source_content: str,
    file_path: str = "",
    model: str = DEFAULT_MODEL,
    max_tokens: int = 4096,
) -> dict:
    """Summarize source content using a prompt template.

    Args:
        template: Prompt template string containing %FILE_CONTENT%.
        source_content: The source code to substitute in.
        file_path: File path to substitute into %FILE_PATH%.
        model: Anthropic model to use.
        max_tokens: Max tokens in the response.

    Returns:
        Dict with keys: model, source_length, response.
    """
    prompt = build_prompt(template, source_content, file_path)
    log.debug("Prompt length: %d chars for %s", len(prompt), file_path or "<inline>")
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    log.info("Calling %s for %s (%d chars)", model, file_path or "<inline>", len(source_content))
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    response_text = message.content[0].text
    log.info("Got response for %s (%d chars)", file_path or "<inline>", len(response_text))
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
) -> dict:
    """Summarize a source file using a prompt template file.

    Returns:
        Dict with keys: model, source_path, source_length, response.
    """
    template = load_template(template_path)
    source_content = load_source(source_path)
    result = summarize_content(template, source_content, source_path, model, max_tokens)
    result["source_path"] = source_path
    return result


def main():
    parser = argparse.ArgumentParser(description="Summarize source code via the Anthropic API.")
    parser.add_argument("--template", required=True, help="Path to prompt template containing %%FILE_CONTENT%%")
    parser.add_argument("--source", required=True, help="Path to source file")
    parser.add_argument("--output", help="Path to write JSON output (default: stdout)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Model to use (default: {DEFAULT_MODEL})")
    parser.add_argument("--max-tokens", type=int, default=4096, help="Max response tokens (default: 4096)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.error("ANTHROPIC_API_KEY environment variable is not set.")
        sys.exit(1)

    log.info("Summarizing %s with template %s", args.source, args.template)
    result = summarize_file(args.template, args.source, args.model, args.max_tokens)
    output = json.dumps(result, indent=2)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output + "\n")
        log.info("Result written to %s", args.output)
    else:
        print(output)


if __name__ == "__main__":
    main()
