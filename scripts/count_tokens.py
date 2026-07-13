#!/usr/bin/env python3
"""Count tokens in text — the single shared token counter for this project.

Three rungs, each self-labelling so callers always know how a number was produced:

1. ``tiktoken`` cl100k_base (default when installed). This is a GPT tokenizer, so
   it is only APPROXIMATE for Claude — treat the absolute number as a proxy. The
   reliable output is the same-method percentage (count both sides the same way
   and the ratio is valid), never the absolute count. For a real Claude count use
   ``count_tokens_exact``.
2. Word-count heuristic (offline fallback when tiktoken is unavailable). Clearly
   labelled as an ESTIMATE — never presented as an exact number.
3. Anthropic API exact count via ``count_tokens_exact`` (opt-in, needs a key and
   network). This is the only rung that returns a true Claude token count.

The default rung is deliberately unchanged so every published percentage stays
comparable across versions — importers get cl100k_base or the heuristic, and the
exact rung is never on the automatic path (measurement must not force the network).

Importable:
    from count_tokens import count_tokens, count_tokens_exact, is_estimate
    n, method = count_tokens("some text")            # offline, self-labelled
    n, method = count_tokens_exact("some text")      # real Claude count (opt-in)

CLI:
    python count_tokens.py input.txt
    cat input.txt | python count_tokens.py
    python count_tokens.py --exact input.txt         # needs ANTHROPIC_API_KEY
"""
from __future__ import annotations

import argparse
import os
import re
import sys

__all__ = ["count_tokens", "count_tokens_exact", "is_estimate"]

# Rough words -> tokens factor used only for the offline fallback.
# 1.3 tokens per whitespace-separated word is a conservative middle-ground for
# mixed English/German prose. It is an estimate, not an exact count.
_HEURISTIC_FACTOR = 1.3

# Default (offline) method labels. Kept stable so is_estimate() and every
# published percentage stay comparable across versions.
_TIKTOKEN_METHOD = "tiktoken:cl100k_base"
_HEURISTIC_METHOD = "heuristic(words*1.3)"

# Lazily-loaded, cached tiktoken encoder. Building the encoder is expensive and
# the advisor/benchmark call count_tokens hundreds of times, so we load it at
# most once per process. ``_ENCODER_LOADED`` distinguishes "not tried yet" from
# "tried and unavailable" (encoder stays None in the latter case).
_ENCODER = None
_ENCODER_LOADED = False


def _get_encoder():
    """Return the cached cl100k_base encoder, or ``None`` if unavailable.

    tiktoken is imported and the encoder built at most once; every later call
    reuses the cached object (or the cached ``None`` when tiktoken is absent).
    """
    global _ENCODER, _ENCODER_LOADED
    if not _ENCODER_LOADED:
        _ENCODER_LOADED = True
        try:
            import tiktoken  # noqa: PLC0415 - optional dependency, imported lazily

            _ENCODER = tiktoken.get_encoding("cl100k_base")
        except Exception:  # ImportError or any tiktoken load-time issue
            _ENCODER = None
    return _ENCODER


def count_tokens(text: str) -> tuple[int, str]:
    """Return ``(count, method)`` using the default offline rung.

    ``method`` is ``"tiktoken:cl100k_base"`` when the GPT tokenizer is available
    (approximate for Claude), otherwise ``"heuristic(words*1.3)"`` to signal the
    number is an estimate. The tuple shape is a load-bearing contract — several
    callers unpack ``n, method = count_tokens(...)``, so it must never change.
    """
    enc = _get_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text)), _TIKTOKEN_METHOD
        except Exception:  # any tiktoken runtime issue -> honest heuristic fallback
            pass
    words = len(re.findall(r"\S+", text))
    return round(words * _HEURISTIC_FACTOR), _HEURISTIC_METHOD


def count_tokens_exact(
    text: str, *, model: str = "claude-haiku-4-5-20251001"
) -> tuple[int, str]:
    """Return ``(count, "anthropic-api:<model>")`` — a real Claude token count.

    Opt-in and never on the automatic path: it lazy-imports ``anthropic``, needs
    ``ANTHROPIC_API_KEY``, and calls ``messages.count_tokens(...).input_tokens``.

    On ANY problem (missing key, missing SDK, network/API error) it prints a
    clear one-line message to stderr and FALLS BACK to :func:`count_tokens`,
    returning that rung's honest label — it never mislabels an offline estimate
    as an exact API count. Because the fallback label is truthful,
    ``is_estimate`` on the returned method still reflects reality.
    """
    method = f"anthropic-api:{model}"

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "[count_tokens] ANTHROPIC_API_KEY not set; falling back to the offline "
            "counter (set a key for an exact Claude count).",
            file=sys.stderr,
        )
        return count_tokens(text)

    try:
        import anthropic  # noqa: PLC0415 - optional dependency, imported lazily

        client = anthropic.Anthropic()
        result = client.messages.count_tokens(
            model=model,
            messages=[{"role": "user", "content": text}],
        )
        return result.input_tokens, method
    except Exception as exc:  # ImportError, auth, network, API error, etc.
        print(
            f"[count_tokens] exact count via '{method}' failed ({exc}); "
            "falling back to the offline counter.",
            file=sys.stderr,
        )
        return count_tokens(text)


def is_estimate(method: str) -> bool:
    """Return ``True`` when ``method`` denotes an approximate/estimated count.

    Only the offline heuristic is an estimate. ``tiktoken:*`` is an approximate
    proxy but still a real tokenization (not an estimate here), and
    ``anthropic-api:*`` is an exact Claude count — both return ``False``.
    """
    return not (method.startswith("tiktoken") or method.startswith("anthropic-api"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Count tokens in a text file or stdin.")
    parser.add_argument("input", nargs="?", help="input file; omit to read from stdin")
    parser.add_argument(
        "--exact",
        action="store_true",
        help="use the Anthropic API for an exact Claude token count "
        "(requires ANTHROPIC_API_KEY; falls back to the offline counter on failure)",
    )
    parser.add_argument(
        "--model",
        default="claude-haiku-4-5-20251001",
        help="model to count against when --exact is used",
    )
    args = parser.parse_args(argv)

    if args.input:
        with open(args.input, encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = sys.stdin.read()

    if args.exact:
        count, method = count_tokens_exact(text, model=args.model)
    else:
        count, method = count_tokens(text)

    label = " (estimate)" if is_estimate(method) else ""
    print(f"{count} tokens{label} [{method}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
