"""Basic smoke tests: the CLI entry points run and return sane types."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "benchmarks"))

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


# --------------------------------------------------------------------------- #
# Honest compression corpus + benchmark runner (benchmarks/compression_corpus.py)
# --------------------------------------------------------------------------- #
def test_corpus_has_redundant_and_unique_samples():
    """The corpus is only 'honest' if it holds BOTH redundant and genuinely
    unique prose — the whole point is to show the floor next to the highs."""
    import compression_corpus  # noqa: E402

    categories = {s.category for s in compression_corpus.load_corpus()}
    assert "redundant" in categories, "corpus is missing redundant_* samples"
    assert "unique" in categories, "corpus is missing unique_* samples"


def test_corpus_benchmark_runs_offline_and_returns_sane_rows():
    """The benchmark runner must run fully offline and report a real-tokenizer
    before/after for every sample and mode, without ever increasing tokens or
    dropping a source number."""
    import compression_corpus  # noqa: E402

    rows = compression_corpus.run_corpus()
    assert rows, "run_corpus produced no rows"
    for r in rows:
        assert isinstance(r["tokens_before"], int) and isinstance(r["tokens_after"], int)
        assert r["tokens_before"] >= r["tokens_after"] >= 0
        assert r["level"] in compression_corpus.LEVELS
        assert isinstance(r["method"], str) and r["method"]
        assert isinstance(r["is_estimate"], bool)
        assert r["numbers_preserved"] is True, (
            f"a source number was dropped for {r['name']} @ {r['level']}"
        )


def test_corpus_selftest_passes():
    """The runner's own offline selftest (redundant compresses more than unique,
    numbers preserved) must pass — it is the honesty contract of the corpus."""
    import compression_corpus  # noqa: E402

    assert compression_corpus.main(["--selftest"]) == 0
