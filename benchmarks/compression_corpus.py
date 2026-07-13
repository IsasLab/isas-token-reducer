#!/usr/bin/env python3
"""Honest Tier-1 before/after benchmark over a mixed compression corpus.

WHY THIS EXISTS
---------------
Most before/after percentages published in this repo were measured on
*deliberately redundant* fixtures, which flatters the tool. This benchmark runs
the free, offline Tier-1 pipeline (`scripts/reduce.py::reduce_text`) over a
DIVERSE corpus that mixes redundant prose with GENUINELY-UNIQUE prose, so the
report shows both realities at once:

  * redundant text  -> large, honest savings (Tier-1's home turf)
  * unique prose     -> a few percent at most, the information-theory floor

That floor is the central hard truth of the project: no lossless offline pass
can shrink text that has no redundancy. Reporting only the redundant wins would
be dishonest; this runner refuses to.

WHAT IT MEASURES
----------------
For every sample and every level (safe / balanced / aggressive) it counts tokens
before and after with the SHARED real tokenizer (`scripts/count_tokens.py`) and
reports the saved percentage. When `tiktoken` is installed the counts are exact
for the cl100k encoding; otherwise the counter falls back to a clearly-labelled
`words*1.3` ESTIMATE. Either way the percentage is computed same-method-both-
sides, so the ratio is reliable even when the absolute number is approximate.
The method label and an `(estimate)` marker are always surfaced.

It also checks a cheap safety invariant on every row: no distinct number that
appeared in the source is missing from the reduced output (Tier-1's "numbers are
never altered" promise). A violation is reported, not hidden.

OFFLINE / NO KEY
----------------
Everything here runs with zero network and no API key. There is no semantic
(Tier-2) measurement in this file on purpose: the semantic tier is lossy and
COSTS tokens to run, so it is a net ledger, not a before/after percentage, and
belongs elsewhere.

USAGE
-----
    python benchmarks/compression_corpus.py                 # table + summary
    python benchmarks/compression_corpus.py --json          # machine-readable
    python benchmarks/compression_corpus.py --level balanced
    python benchmarks/compression_corpus.py --selftest      # offline assertions
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from count_tokens import count_tokens, is_estimate  # noqa: E402
from reduce import reduce_text  # noqa: E402

CORPUS_DIR = _HERE / "corpus"
LEVELS = ("safe", "balanced", "aggressive")

# Same simple numeric probe the safety tests use: every distinct value that
# appears in the source must still appear in the reduced output.
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    return set(_NUM_RE.findall(text))


class Sample(NamedTuple):
    name: str
    category: str  # "redundant" | "unique" | "mixed" | "other"
    path: Path
    text: str


def _category_of(filename: str) -> str:
    prefix = filename.split("_", 1)[0]
    return prefix if prefix in {"redundant", "unique", "mixed"} else "other"


def load_corpus(corpus_dir: Path = CORPUS_DIR) -> list[Sample]:
    """Load every ``*.txt`` sample from the corpus directory, sorted by name."""
    samples: list[Sample] = []
    for path in sorted(corpus_dir.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        samples.append(
            Sample(name=path.name, category=_category_of(path.name), path=path, text=text)
        )
    return samples


def run_sample(sample: Sample, levels: tuple[str, ...] = LEVELS) -> list[dict]:
    """Reduce one sample at each level; return a row dict per level."""
    before_tokens, method = count_tokens(sample.text)
    src_numbers = _numbers(sample.text)
    rows: list[dict] = []
    for level in levels:
        reduced = reduce_text(sample.text, level=level)
        after_tokens, _ = count_tokens(reduced)
        saved = before_tokens - after_tokens
        saved_pct = (saved / before_tokens * 100.0) if before_tokens else 0.0
        # Safety probe: did any distinct source number vanish?
        numbers_preserved = src_numbers.issubset(_numbers(reduced))
        rows.append(
            {
                "name": sample.name,
                "category": sample.category,
                "level": level,
                "tokens_before": before_tokens,
                "tokens_after": after_tokens,
                "saved": saved,
                "saved_pct": round(saved_pct, 1),
                "chars_before": len(sample.text),
                "chars_after": len(reduced),
                "method": method,
                "is_estimate": is_estimate(method),
                "numbers_preserved": numbers_preserved,
            }
        )
    return rows


def run_corpus(
    levels: tuple[str, ...] = LEVELS, corpus_dir: Path = CORPUS_DIR
) -> list[dict]:
    """Run the whole corpus and return a flat list of per-(sample, level) rows."""
    rows: list[dict] = []
    for sample in load_corpus(corpus_dir):
        rows.extend(run_sample(sample, levels))
    return rows


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def summarize(rows: list[dict]) -> dict:
    """Average saved% per (category, level) and per category — the honest core.

    The headline the caller should read: redundant categories save a lot, unique
    prose barely moves. Both are correct.
    """
    categories = sorted({r["category"] for r in rows})
    levels = sorted({r["level"] for r in rows}, key=lambda l: LEVELS.index(l) if l in LEVELS else 99)
    by_cat_level: dict[str, dict[str, float]] = {}
    by_cat: dict[str, float] = {}
    for cat in categories:
        by_cat_level[cat] = {}
        for level in levels:
            pcts = [r["saved_pct"] for r in rows if r["category"] == cat and r["level"] == level]
            by_cat_level[cat][level] = round(_mean(pcts), 1)
        by_cat[cat] = round(_mean([r["saved_pct"] for r in rows if r["category"] == cat]), 1)
    return {
        "categories": categories,
        "levels": levels,
        "mean_saved_pct_by_category_level": by_cat_level,
        "mean_saved_pct_by_category": by_cat,
        "any_estimate": any(r["is_estimate"] for r in rows),
        "all_numbers_preserved": all(r["numbers_preserved"] for r in rows),
    }


def format_report(rows: list[dict]) -> str:
    if not rows:
        return "(no corpus samples found)"
    method = rows[0]["method"]
    est = "  (ESTIMATE - install tiktoken for exact counts)" if rows[0]["is_estimate"] else ""
    lines: list[str] = []
    lines.append(f"Tier-1 before/after over the honest corpus  [tokenizer: {method}]{est}")
    lines.append("")
    header = f"{'sample':<34} {'cat':<9} {'level':<11} {'before':>7} {'after':>7} {'saved%':>7} {'nums':>5}"
    lines.append(header)
    lines.append("-" * len(header))
    last_name = None
    for r in rows:
        name = r["name"] if r["name"] != last_name else ""
        last_name = r["name"]
        nums = "ok" if r["numbers_preserved"] else "LOST"
        lines.append(
            f"{name:<34} {r['category']:<9} {r['level']:<11} "
            f"{r['tokens_before']:>7} {r['tokens_after']:>7} {r['saved_pct']:>7.1f} {nums:>5}"
        )

    summary = summarize(rows)
    lines.append("")
    lines.append("Mean saved% by category (the honest headline):")
    for cat in summary["categories"]:
        per_level = "  ".join(
            f"{lvl}={summary['mean_saved_pct_by_category_level'][cat][lvl]:>5.1f}%"
            for lvl in summary["levels"]
        )
        lines.append(f"  {cat:<10} overall={summary['mean_saved_pct_by_category'][cat]:>5.1f}%   {per_level}")

    lines.append("")
    lines.append("Read this honestly:")
    lines.append("  * 'redundant' rows are Tier-1's best case; they do NOT generalize to unique prose.")
    lines.append("  * 'unique' rows sit near the information-theory floor (~1-3%). That is correct,")
    lines.append("    not a bug: a lossless offline pass cannot shrink text that has no redundancy.")
    lines.append("  * The percentage is same-method-both-sides and reliable; the absolute token")
    lines.append("    count is only as exact as the tokenizer named above.")
    lines.append("  * Tier-1 only. The lossy semantic tier costs tokens and is a net ledger, not a %.")
    if not summary["all_numbers_preserved"]:
        lines.append("")
        lines.append("  !! WARNING: a source number went missing in some reduced output (see 'nums=LOST').")
    return "\n".join(lines)


def _selftest() -> int:
    samples = load_corpus()
    assert samples, "corpus is empty — expected sample .txt files under corpus/"
    cats = {s.category for s in samples}
    assert "redundant" in cats, "corpus must contain at least one redundant_* sample"
    assert "unique" in cats, "corpus must contain at least one unique_* sample"

    rows = run_corpus()
    assert rows, "run_corpus produced no rows"

    # Structural sanity on every row.
    for r in rows:
        assert r["tokens_before"] >= 0 and r["tokens_after"] >= 0
        assert r["tokens_after"] <= r["tokens_before"], (
            f"reduction increased token count for {r['name']} @ {r['level']}"
        )
        assert isinstance(r["method"], str) and r["method"], "missing tokenizer method label"

    # The safety promise: Tier-1 never drops a distinct source number.
    lost = [(r["name"], r["level"]) for r in rows if not r["numbers_preserved"]]
    assert not lost, f"Tier-1 dropped a source number on: {lost}"

    # The honesty headline: redundant text compresses more than unique prose.
    summary = summarize(rows)
    red = summary["mean_saved_pct_by_category"]["redundant"]
    uniq = summary["mean_saved_pct_by_category"]["unique"]
    assert red > uniq, (
        f"expected redundant ({red}%) to compress more than unique ({uniq}%)"
    )
    # Unique prose must be near the information-theory floor, not a big number.
    assert uniq < 20.0, (
        f"unique-prose savings ({uniq}%) are implausibly high — check the corpus is "
        f"genuinely non-redundant"
    )

    print("selftest OK")
    print(f"  samples={len(samples)}  categories={sorted(cats)}")
    print(f"  mean saved%: redundant={red}%  unique={uniq}%  (redundant > unique, as expected)")
    print(f"  tokenizer method={rows[0]['method']}  estimate={rows[0]['is_estimate']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="emit rows + summary as JSON")
    parser.add_argument("--level", choices=list(LEVELS), help="restrict to a single level")
    parser.add_argument("--selftest", action="store_true", help="run offline assertions and exit")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()

    levels = (args.level,) if args.level else LEVELS
    rows = run_corpus(levels)

    if args.json:
        print(json.dumps({"rows": rows, "summary": summarize(rows)}, indent=2))
    else:
        print(format_report(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
