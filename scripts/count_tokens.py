#!/usr/bin/env python3
"""Count tokens in text.

Uses `tiktoken` when it is installed (exact for OpenAI-family encodings and a
very close proxy for Claude). When tiktoken is not available it falls back to a
word-count heuristic that is clearly labelled as an ESTIMATE — never presented
as an exact number.

Importable:
    from count_tokens import count_tokens
    n, method = count_tokens("some text")

CLI:
    python count_tokens.py input.txt
    cat input.txt | python count_tokens.py
"""
from __future__ import annotations

import argparse
import re
import sys

# Rough chars/words -> tokens factor used only for the offline fallback.
# 1.3 tokens per whitespace-separated word is a conservative middle-ground for
# mixed English/German prose. It is an estimate, not an exact count.
_HEURISTIC_FACTOR = 1.3


def count_tokens(text: str) -> tuple[int, str]:
    """Return ``(count, method)``.

    ``method`` is ``"tiktoken:<encoding>"`` when exact, otherwise
    ``"heuristic(words*1.3)"`` to signal the number is an estimate.
    """
    try:
        import tiktoken  # noqa: PLC0415 - optional dependency, imported lazily

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text)), "tiktoken:cl100k_base"
    except Exception:  # ImportError or any tiktoken runtime issue
        words = len(re.findall(r"\S+", text))
        return round(words * _HEURISTIC_FACTOR), "heuristic(words*1.3)"


def is_estimate(method: str) -> bool:
    return not method.startswith("tiktoken")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Count tokens in a text file or stdin.")
    parser.add_argument("input", nargs="?", help="input file; omit to read from stdin")
    args = parser.parse_args(argv)

    if args.input:
        with open(args.input, encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = sys.stdin.read()

    count, method = count_tokens(text)
    label = " (estimate)" if is_estimate(method) else ""
    print(f"{count} tokens{label} [{method}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
