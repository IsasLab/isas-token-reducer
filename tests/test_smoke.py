"""Basic smoke tests: the CLI entry points run and return sane types."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from count_tokens import count_tokens, is_estimate  # noqa: E402


def test_count_tokens_returns_int_and_method():
    n, method = count_tokens("This is a short test sentence.")
    assert isinstance(n, int)
    assert n > 0
    assert isinstance(method, str)
    assert isinstance(is_estimate(method), bool)


def test_count_tokens_empty_string():
    n, _ = count_tokens("")
    assert n == 0


def test_reduce_cli_importable():
    # Guards against accidental syntax errors / broken imports shipping to main.
    import reduce  # noqa: F401
    import classify_task  # noqa: F401
