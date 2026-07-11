#!/usr/bin/env python3
"""Answer-stability-under-input-compression benchmark for isas-token-reducer (GSM8K).

WHY "ANSWER STABILITY" AND NOT "REASONING PRESERVATION"
-------------------------------------------------------
GSM8K is a widely published, very likely memorized dataset for frontier
models. If a compressed question still yields the right answer, we cannot
distinguish "the model reasoned through the compressed text" from "the
compressed surface form still triggered retrieval of a memorized solution".
Conversely, a drop under compression could be memorization-retrieval being
disturbed rather than reasoning being disrupted. So the honest headline claim
this benchmark supports is narrow: **does input compression change the final
answer the model emits** (answer stability), NOT the stronger "reasoning is
preserved". The metric is named accordingly throughout (answer_stability_delta).
To probe reasoning specifically you would need a held-out/synthetic problem set
(out of scope here; noted in the "could be wrong" list).

WHAT THIS MEASURES
-------------------
`scripts/reduce.py::reduce_text()` compresses text *before* an LLM reads it
(input-side reduction). The risk that matters for a tool like this is not
"does it save tokens" (that part is trivially true and already measured by
`count_tokens.py`) — it is "does compressing the question change whether the
model still gets the right answer". This script tests exactly that, on
GSM8K grade-school math word problems, across:

    * baseline           (unmodified question)
    * safe               reduce_text(level="safe")
    * balanced           reduce_text(level="balanced")
    * aggressive         reduce_text(level="aggressive")
    * each of the above  with tier2=True as well (tier2=False is implicit
                          in the plain level runs)

7 conditions total: baseline, safe, safe+tier2, balanced, balanced+tier2,
aggressive, aggressive+tier2. Every condition is run over the *same* sampled
question IDs so the comparison is paired.

WHAT THIS SCRIPT CAN DO ON A MACHINE WITHOUT AN ANTHROPIC API KEY
-------------------------------------------------------------------
The full accuracy comparison requires calling a Claude model to *solve* each
question, which requires `ANTHROPIC_API_KEY` + the `anthropic` SDK. Those are
NOT dependencies of this script and this script does not assume they exist.
Everything else runs fully offline and is exercised by `--selftest`:

    1. Dataset loading (download + cache + integrity check, with a bundled
       offline fallback sample so `--selftest` never needs the network).
    2. The exact-match answer extractor, unit-tested against GSM8K's
       `#### <answer>` gold format and realistic free-text model output
       (negatives, decimals, thousands separators, `$`/`%`, "the answer is").
    3. A reduction-invariance / numeric-fidelity check: for every sampled
       question and every level, verify reduce_text() does not silently drop
       a number that appeared in the original — this is the cheapest possible
       proxy for "did the compression corrupt something the answer depends
       on", and it needs no API calls at all.
    4. The statistics helpers (Wilson CI, exact McNemar test, required-n
       power calculations) are unit-tested against known reference values.

If `ANTHROPIC_API_KEY` / `anthropic` are missing, `run` prints the offline
diagnostics, then a clearly-labelled "SKIPPED: full accuracy run requires
ANTHROPIC_API_KEY" block, and writes a partial results file with
status="skipped_no_api". It never fabricates an accuracy number.

DATA SOURCE
-----------
GSM8K test split, downloaded from the official OpenAI repository, PINNED to a
specific commit SHA (not a moving `master` ref) and verified by sha256 so the
dataset is byte-reproducible over time:
    https://raw.githubusercontent.com/openai/grade-school-math/<SHA>/grade_school_math/data/test.jsonl
License: MIT, Copyright (c) 2021 OpenAI (github.com/openai/grade-school-math).
Verified 2026-07-11: 1319 lines, JSONL, fields {"question", "answer"}, answer
ends with "#### <final numeric answer>". Cached (raw bytes, LF preserved) to
`benchmarks/data/gsm8k_test.jsonl` after first download; both the download and
the cache are sha256-checked against GSM8K_SHA256 before use, and the hash used
is recorded in the output JSON. A hash mismatch is a hard error, not a warning.

USAGE
-----
    python benchmarks/reasoning_preservation.py --selftest
    python benchmarks/reasoning_preservation.py --power-calc
    python benchmarks/reasoning_preservation.py --n 200 --seed 1337
    python benchmarks/reasoning_preservation.py --n 1319 --out results.json

With no ANTHROPIC_API_KEY set, the last two forms still run the offline
dataset load + reduction diagnostics + numeric-fidelity checks, print them,
and then print a "SKIPPED" block instead of an accuracy table.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace as dataclass_replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Import the real reduce_text()/count_tokens() from scripts/, not a re-impl.
# --------------------------------------------------------------------------- #
_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts"
sys.path.insert(0, str(_SCRIPTS))
from reduce import reduce_text, load_fillers, load_substitutions  # noqa: E402
from count_tokens import count_tokens  # noqa: E402

# Loaded once and reused across every reduce_text() call in this module — the
# default (fillers=None/subs=None) path re-reads and re-parses these files
# from disk on every call, which is wasteful across hundreds of records.
_FILLERS = load_fillers()
_SUBS = load_substitutions()

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
# Pinned to the exact commit that last touched the data file, NOT `master`, so
# the dataset is reproducible even if upstream changes. sha256 verified below.
GSM8K_COMMIT = "b0bb162abedc65e1fdd8e93ed090fd7598ee68bc"
GSM8K_URL = (
    "https://raw.githubusercontent.com/openai/grade-school-math/"
    f"{GSM8K_COMMIT}/grade_school_math/data/test.jsonl"
)
# sha256 of the raw bytes (LF line endings) of the file at GSM8K_COMMIT.
# Computed 2026-07-11. A mismatch is a hard error — see load_gsm8k.
GSM8K_SHA256 = "3730d312f6e3440559ace48831e51066acaca737f6eabec99bccb9e4b3c39d14"
GSM8K_LICENSE_NOTE = (
    "GSM8K test split, MIT License, Copyright (c) 2021 OpenAI "
    "(github.com/openai/grade-school-math)."
)
EXPECTED_TEST_COUNT = 1319  # verified against the pinned commit on 2026-07-11
DATA_CACHE = Path(__file__).resolve().parent / "data" / "gsm8k_test.jsonl"

DEFAULT_SEED = 1337
DEFAULT_N = 200
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_TOKENS = 512
LEVELS = ("safe", "balanced", "aggressive")
TIER2_CHAR_THRESHOLD = 1200  # must match reduce_text's default

PROMPT_TEMPLATE = (
    "Solve the following grade-school math word problem. Think step by "
    "step, then on the very last line output exactly:\n"
    "Answer: <number>\n\n"
    "Problem: {question}"
)

# --------------------------------------------------------------------------- #
# Offline fallback sample — hand-authored, NOT official GSM8K data. Used only
# when the network is unreachable / --offline is passed. Clearly out of scope
# for any real accuracy claim; exists so --selftest and smoke tests never
# depend on network access.
# --------------------------------------------------------------------------- #
FALLBACK_SAMPLE: list[dict[str, str]] = [
    {"question": "Maria has 14 pencils. She gives 5 to her brother and buys 8 more. How many pencils does Maria have now?",
     "answer": "Maria starts with 14 pencils.\n14 - 5 = <<14-5=9>>9 pencils after giving some away.\n9 + 8 = <<9+8=17>>17 pencils after buying more.\n#### 17"},
    {"question": "A bakery sells cupcakes for $3 each. Tom buys 6 cupcakes and pays with a $20 bill. How much change does he receive?",
     "answer": "6 cupcakes cost 6 * 3 = <<6*3=18>>18 dollars.\n20 - 18 = <<20-18=2>>2 dollars in change.\n#### 2"},
    {"question": "A water tank holds 500 liters. It is currently 40% full. How many liters of water are in the tank?",
     "answer": "40% of 500 is 500 * 0.4 = <<500*0.4=200>>200 liters.\n#### 200"},
    {"question": "James reads 3 books a week. Each book has 220 pages. How many pages does he read in 4 weeks?",
     "answer": "Pages per week: 3 * 220 = <<3*220=660>>660.\nOver 4 weeks: 660 * 4 = <<660*4=2640>>2640 pages.\n#### 2640"},
    {"question": "A recipe needs 2.5 cups of flour per batch. How many cups of flour are needed for 4 batches?",
     "answer": "2.5 * 4 = <<2.5*4=10>>10 cups.\n#### 10"},
    {"question": "A theater has 1,200 seats. If 950 tickets were sold for tonight's show, how many seats are empty?",
     "answer": "1200 - 950 = <<1200-950=250>>250 empty seats.\n#### 250"},
    {"question": "Lena had $30. She spent $12 on lunch and then earned $18 walking dogs. How much money does she have now?",
     "answer": "30 - 12 = <<30-12=18>>18 dollars after lunch.\n18 + 18 = <<18+18=36>>36 dollars after dog walking.\n#### 36"},
    {"question": "A train travels 60 miles per hour for 3.5 hours. How many miles does it travel in total?",
     "answer": "60 * 3.5 = <<60*3.5=210>>210 miles.\n#### 210"},
    {"question": "A farmer has 45 chickens. Each chicken lays 1 egg a day. He sells eggs in cartons of 12. How many full cartons can he fill in one day?",
     "answer": "45 eggs a day. 45 / 12 = 3 remainder 9, so 3 full cartons.\n#### 3"},
    {"question": "Two friends split a restaurant bill of $84 evenly, then each leaves a $6 tip. How much does each friend pay in total?",
     "answer": "84 / 2 = <<84/2=42>>42 dollars each before tip.\n42 + 6 = <<42+6=48>>48 dollars each total.\n#### 48"},
]

# --------------------------------------------------------------------------- #
# Dataset loading
# --------------------------------------------------------------------------- #
class DatasetIntegrityError(RuntimeError):
    """Raised when downloaded/cached GSM8K bytes fail the sha256 check."""


def _download_gsm8k(timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(GSM8K_URL, headers={"User-Agent": "isas-token-reducer-benchmark"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read()


def _parse_jsonl_bytes(raw: bytes) -> list[dict[str, str]]:
    text = raw.decode("utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def load_gsm8k(
    cache_path: Path = DATA_CACHE,
    allow_download: bool = True,
    offline: bool = False,
) -> tuple[list[dict[str, str]], str]:
    """Return (records, source_label).

    source_label is one of "cache", "download", or a "fallback(...)" reason so
    callers/reports can be explicit about whether real GSM8K data was used.

    Integrity: the raw bytes (from cache or download) are sha256-checked against
    GSM8K_SHA256. A mismatch raises DatasetIntegrityError (hard failure) rather
    than warn-and-proceed — a silently-changed dataset would invalidate the run.
    Bytes are cached verbatim (binary write, LF preserved) so the on-disk hash
    equals the upstream hash on every platform, including Windows.
    """
    if offline:
        return list(FALLBACK_SAMPLE), "fallback(offline-flag)"

    if cache_path.exists():
        cached = cache_path.read_bytes()
        digest = hashlib.sha256(cached).hexdigest()
        if digest == GSM8K_SHA256:
            return _parse_jsonl_bytes(cached), "cache"
        sys.stderr.write(
            f"[dataset] cached file sha256 {digest[:12]}... != expected "
            f"{GSM8K_SHA256[:12]}...; discarding cache and re-downloading.\n"
        )

    if allow_download:
        try:
            raw = _download_gsm8k()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            sys.stderr.write(f"[dataset] download failed ({exc}); using offline fallback sample.\n")
            return list(FALLBACK_SAMPLE), "fallback(download-failed)"
        digest = hashlib.sha256(raw).hexdigest()
        if digest != GSM8K_SHA256:
            raise DatasetIntegrityError(
                f"downloaded GSM8K from pinned commit {GSM8K_COMMIT} but sha256 "
                f"{digest} != expected {GSM8K_SHA256}. Refusing to proceed with "
                "an unverified dataset."
            )
        records = _parse_jsonl_bytes(raw)
        if len(records) != EXPECTED_TEST_COUNT:  # belt-and-suspenders; hash already guarantees this
            raise DatasetIntegrityError(
                f"got {len(records)} records, expected {EXPECTED_TEST_COUNT} "
                "despite matching hash — parser or constant is out of sync."
            )
        try:  # cache is an optimization; a read-only data dir must not abort the run
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(raw)  # verbatim: preserves LF so cache hash == upstream hash
        except OSError as exc:
            sys.stderr.write(f"[dataset] WARNING: could not cache dataset to {cache_path}: {exc} "
                             "(continuing with in-memory data).\n")
        return records, "download"

    return list(FALLBACK_SAMPLE), "fallback(no-download-allowed)"


def looks_like_whole_document_json(text: str) -> bool:
    """Replicate reduce_text's whole-document-JSON short-circuit check.

    If this is True for a question, reduce_text() will minify it as JSON and
    skip every prose pass entirely (see scripts/reduce.py). GSM8K questions
    are prose and this should never fire in practice, but the benchmark must
    not silently accept a "reduction" that is actually a no-op JSON minify.
    """
    stripped = text.strip()
    if stripped[:1] not in "{[":
        return False
    try:
        json.loads(stripped)
        return True
    except ValueError:
        return False


def select_sample(records: list[dict[str, str]], n: int, seed: int) -> list[dict[str, Any]]:
    """Deterministically select n records by index, skipping any record whose
    question would trigger the whole-document-JSON short-circuit (see above).
    Each returned dict carries a stable 'id' = its index in `records`.
    """
    indexed = list(enumerate(records))
    rng = random.Random(seed)
    order = list(range(len(indexed)))
    rng.shuffle(order)

    picked: list[dict[str, Any]] = []
    skipped_json = 0
    for idx in order:
        if len(picked) >= n:
            break
        i, rec = indexed[idx]
        q = rec.get("question", "")
        if looks_like_whole_document_json(q):
            skipped_json += 1
            continue
        picked.append({"id": i, "question": q, "answer": rec.get("answer", "")})
    if skipped_json:
        sys.stderr.write(f"[dataset] skipped {skipped_json} question(s) that look like whole-document JSON.\n")
    picked.sort(key=lambda r: r["id"])  # stable, reproducible ordering
    return picked


# --------------------------------------------------------------------------- #
# Exact-match answer extraction
# --------------------------------------------------------------------------- #
# Matches things like: 72   -10   3.5   2,125   $80,000   20%
# The leading '-' / '$' only counts when NOT preceded by a word char or '.', so
# unspaced subtraction like "18-5" yields 18 and 5 (not 18 and -5), and "v2"
# does not spuriously match. A '-' after whitespace/start (e.g. gold "#### -10")
# is still a valid sign. (Fixes critic point 10.)
_NUM_CORE = r"\d[\d,]*(?:\.\d+)?%?"
_NUM_RE = re.compile(r"(?<![\w.])-?\$?" + _NUM_CORE)

_GOLD_RE = re.compile(r"####\s*(-?\$?" + _NUM_CORE + r")")

_MARKER_ALTS = ["final answer is", "final answer", "the answer is", "answer is", "answer"]
# Optional hedge/connector words the model may insert between the marker and the
# number ("the answer is actually 48"). Without this the marker match silently
# fails on the corrected answer and keeps the earlier discarded guess. (point 7)
_HEDGE = r"(?:actually|really|indeed|therefore|thus|now|approximately|about|roughly|around|equal to|equals|=)?"
_ANSWER_MARKER_RE = re.compile(
    r"(?:" + "|".join(_MARKER_ALTS) + r")\s*[:\-]?\s*" + _HEDGE + r"\s*(-?\$?" + _NUM_CORE + r")",
    re.IGNORECASE,
)


def normalize_number(raw: str | None) -> float | None:
    """Turn a matched number token into a float, or None if it can't be parsed.

    Handles: leading '-', leading '$', thousands-comma separators, trailing
    '%', and stray punctuation the caller may not have stripped.
    """
    if raw is None:
        return None
    s = raw.strip().rstrip(".,;:!?)。")  # incl. common trailing punctuation
    s = s.lstrip("(")
    s = s.replace("$", "").replace(",", "").replace("%", "").strip()
    if s in ("", "-", "."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def extract_gold_answer(answer_field: str) -> float | None:
    """Extract the final numeric answer from a GSM8K 'answer' field
    (format: reasoning steps, then '#### <number>')."""
    matches = _GOLD_RE.findall(answer_field)
    if not matches:
        return None
    return normalize_number(matches[-1])


def extract_predicted_answer(text: str | None) -> float | None:
    """Extract a model's final numeric answer from free-text output.

    Our prompt mandates a final line 'Answer: <number>', so the last line is the
    authoritative source. Strategy (in priority order):
      1. A marker match ('answer is / final answer / answer:') ON THE LAST
         non-blank line — take the LAST such match on that line. This anchors to
         the model's committed final answer and ignores earlier scratch guesses.
      2. Otherwise, the last marker match ANYWHERE in the text. Because the
         marker regex now tolerates hedge words ("answer is actually 48"), a
         later corrected guess wins over an earlier discarded one. (point 7)
      3. Otherwise, the last standalone number anywhere (weak fallback).
      4. If no number is found at all, return None (unparseable).
    """
    if not text:
        return None
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if lines:
        last_line = lines[-1]
        last_line_markers = _ANSWER_MARKER_RE.findall(last_line)
        if last_line_markers:
            return normalize_number(last_line_markers[-1])
    marker_matches = _ANSWER_MARKER_RE.findall(text)
    if marker_matches:
        return normalize_number(marker_matches[-1])
    all_nums = _NUM_RE.findall(text)
    if all_nums:
        return normalize_number(all_nums[-1])
    return None


def answers_match(gold: float | None, pred: float | None, tol: float = 1e-4) -> bool:
    if gold is None or pred is None:
        return False
    return abs(gold - pred) < tol


# --------------------------------------------------------------------------- #
# Reduction-invariance / numeric-fidelity check (fully offline)
# --------------------------------------------------------------------------- #
def numbers_in(text: str) -> set[str]:
    """Set of normalized numeric literals appearing in text (commas stripped)."""
    out = set()
    for m in _NUM_RE.findall(text):
        n = normalize_number(m)
        if n is not None:
            # Format consistently so 5 and 5.0 compare equal as strings too.
            out.add(repr(n))
    return out


def check_numeric_fidelity(original: str, reduced: str) -> list[str]:
    """Return the list of numbers present in `original` that vanished from
    `reduced`. Empty list == no detectable numeric-fidelity violation.

    This is a proxy, not a proof: it only catches the case where reduction
    deletes a fact wholesale (e.g. a near-duplicate paragraph carrying a
    distinct number gets removed as "redundant"). It cannot catch a reduction
    that garbles a number in place (e.g. transposes digits) — reduce_text's
    own design (whitespace/phrase/dedup passes only) makes that class of bug
    structurally unlikely, but this check does not rule it out.
    """
    orig_nums = numbers_in(original)
    red_nums = numbers_in(reduced)
    return sorted(orig_nums - red_nums)


@dataclass
class ReductionDiagnostics:
    level: str
    n_items: int = 0
    n_changed: int = 0
    n_numeric_fidelity_violations: int = 0
    n_exceeds_tier2_threshold: int = 0
    mean_char_delta_pct: float = 0.0
    examples: list[tuple[str, str]] = field(default_factory=list)
    violation_examples: list[tuple[int, str, str, list[str]]] = field(default_factory=list)


def run_reduction_diagnostics(sample: list[dict[str, Any]], level: str, tier2: bool = False) -> ReductionDiagnostics:
    diag = ReductionDiagnostics(level=level + ("+tier2" if tier2 else ""))
    diag.n_items = len(sample)
    total_pct = 0.0
    for rec in sample:
        q = rec["question"]
        if len(q) > TIER2_CHAR_THRESHOLD:
            diag.n_exceeds_tier2_threshold += 1
        red = reduce_text(q, level=level, fillers=_FILLERS, subs=_SUBS, tier2=tier2)
        if red != q:
            diag.n_changed += 1
            if len(diag.examples) < 3:
                diag.examples.append((q, red))
        violations = check_numeric_fidelity(q, red)
        if violations:
            diag.n_numeric_fidelity_violations += 1
            diag.violation_examples.append((rec["id"], q, red, violations))
        pct = ((len(q) - len(red)) / len(q) * 100) if q else 0.0
        total_pct += pct
    diag.mean_char_delta_pct = total_pct / diag.n_items if diag.n_items else 0.0
    return diag


# --------------------------------------------------------------------------- #
# Statistics: Wilson CI, exact McNemar test, required-n power calculations.
# No scipy/statsmodels dependency (neither is installed in this environment).
# --------------------------------------------------------------------------- #
def _norm_ppf(p: float) -> float:
    """Inverse CDF of the standard normal distribution (Acklam's algorithm,
    relative error < 1.15e-9). Self-contained so we don't need scipy."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00]
    p_low = 0.02425
    p_high = 1 - p_low
    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p <= p_high:
        q = p - 0.5
        r = q * q
        return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
               (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)


def wilson_ci(k: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion k/n."""
    if n == 0:
        return (0.0, 1.0)
    z = _norm_ppf(1 - (1 - confidence) / 2)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def required_n_two_proportion(p1: float, p2: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """Per-arm n for an UNPAIRED two-proportion z-test to detect p1 vs p2.
    This is a conservative upper bound: it ignores the positive correlation
    you get from running the SAME questions through baseline and treatment
    (a paired design, see mcnemar_exact_p / required_n_mcnemar, is more
    powerful for a fixed n whenever that correlation is positive)."""
    z_alpha = _norm_ppf(1 - alpha / 2)
    z_beta = _norm_ppf(power)
    var_sum = p1 * (1 - p1) + p2 * (1 - p2)
    n = ((z_alpha + z_beta) ** 2 * var_sum) / (p1 - p2) ** 2
    return math.ceil(n)


def required_n_mcnemar(psi: float, d: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """Approximate total-pairs n for McNemar's exact test (Connor, 1987) to
    detect a net paired accuracy difference `d` given a discordant-pair rate
    `psi` (psi = P(baseline correct & treatment wrong) + P(baseline wrong &
    treatment correct); d = the difference between those two probabilities).

    psi is NOT knowable in advance for this benchmark — we don't know how
    often reduction flips an answer either direction until we run it. This
    function exists so the required-n claim is falsifiable/inspectable
    rather than asserted; see --power-calc for a table across plausible psi.
    """
    if d <= 0 or psi <= d * d:
        raise ValueError("require 0 < d and psi > d^2")
    z_alpha = _norm_ppf(1 - alpha / 2)
    z_beta = _norm_ppf(power)
    n = (z_alpha * math.sqrt(psi) + z_beta * math.sqrt(psi - d * d)) ** 2 / (d * d)
    return math.ceil(n)


def min_detectable_d_mcnemar(psi: float, n: int, alpha: float = 0.05, power: float = 0.8) -> float | None:
    """Smallest net paired difference d that `n` pairs can detect at the given
    power, for observed discordant-pair rate psi. Inverts required_n_mcnemar by
    bisection (required_n is monotonically decreasing in d on (0, sqrt(psi))).
    Returns None if psi is 0 (no discordant pairs => nothing is estimable)."""
    if n <= 0 or psi <= 0:
        return None
    hi = math.sqrt(psi) * (1 - 1e-9)
    # required_n at d->hi is ~z_alpha^2 (small); if even that needs more than n,
    # nothing is detectable at this psi/n.
    if required_n_mcnemar(psi, hi, alpha, power) > n:
        return None
    lo = 1e-6
    for _ in range(100):
        mid = (lo + hi) / 2
        try:
            need = required_n_mcnemar(psi, mid, alpha, power)
        except ValueError:
            lo = mid
            continue
        if need > n:      # mid too small to detect with n pairs -> need bigger d
            lo = mid
        else:
            hi = mid
    return hi


def mcnemar_exact_p(b: int, c: int) -> float:
    """Exact two-sided McNemar test p-value on discordant pair counts.
    b = #(baseline correct, treatment wrong), c = #(baseline wrong, treatment correct).
    Implemented as an exact binomial(n=b+c, p=0.5) two-sided test via
    math.comb, so no scipy dependency."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)

    def binom_pmf(x: int, n: int) -> float:
        return math.comb(n, x) / (2 ** n)

    p_le_k = sum(binom_pmf(x, n) for x in range(0, k + 1))
    return min(1.0, 2 * p_le_k)


# --------------------------------------------------------------------------- #
# API-backed run (gated: only executes if anthropic + ANTHROPIC_API_KEY exist)
# --------------------------------------------------------------------------- #
def api_available() -> tuple[bool, str]:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False, "ANTHROPIC_API_KEY is not set"
    try:
        import anthropic  # noqa: F401,PLC0415
    except ImportError:
        return False, "the 'anthropic' package is not installed"
    return True, "ok"


def transform_question(question: str, level: str | None, tier2: bool) -> str:
    if level is None:
        return question
    return reduce_text(question, level=level, fillers=_FILLERS, subs=_SUBS, tier2=tier2)


@dataclass
class ItemResult:
    id: int
    condition: str
    gold: float | None
    predicted: float | None
    correct: bool
    input_chars: int
    input_tokens: int
    token_method: str
    latency_s: float
    raw_response: str = ""
    error: str | None = None


DEFAULT_MAX_RETRIES = 3  # transient-error retries before an item is marked errored


def call_model(client: Any, model: str, question_text: str, max_tokens: int) -> tuple[str, float]:
    prompt = PROMPT_TEMPLATE.format(question=question_text)
    t0 = time.monotonic()
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0,  # greedy decoding: minimizes run-to-run variance so the
                         # ONLY intended difference between conditions is the
                         # reduced-vs-original input text, not sampling noise.
        messages=[{"role": "user", "content": prompt}],
    )
    latency = time.monotonic() - t0
    text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
    return text, latency


def call_model_with_retry(
    client: Any, model: str, question_text: str, max_tokens: int, max_retries: int,
) -> tuple[str, float]:
    """Call the model, retrying transient errors with exponential backoff before
    giving up. Only after retries are exhausted does the caller mark the item as
    errored — so a single rate-limit blip does not get miscounted as an accuracy
    change (see critic point 1)."""
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return call_model(client, model, question_text, max_tokens)
        except Exception as exc:  # noqa: BLE001 - SDK raises a variety of types
            last_exc = exc
            if attempt < max_retries:
                time.sleep(min(2 ** attempt, 8))
    raise last_exc  # type: ignore[misc]


def run_condition(
    client: Any,
    model: str,
    sample: list[dict[str, Any]],
    condition: str,
    level: str | None,
    tier2: bool,
    max_tokens: int,
    sleep_s: float,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> list[ItemResult]:
    """Non-interleaved: run every item of one condition, then return."""
    results: list[ItemResult] = []
    for rec in sample:
        results.append(_run_one(client, model, rec, condition, level, tier2, max_tokens, max_retries))
        if sleep_s:
            time.sleep(sleep_s)
    return results


def _run_one(
    client: Any, model: str, rec: dict[str, Any], condition: str,
    level: str | None, tier2: bool, max_tokens: int, max_retries: int,
) -> ItemResult:
    q_used = transform_question(rec["question"], level, tier2)
    gold = extract_gold_answer(rec["answer"])
    n_tokens, method = count_tokens(PROMPT_TEMPLATE.format(question=q_used))
    try:
        raw, latency = call_model_with_retry(client, model, q_used, max_tokens, max_retries)
        pred = extract_predicted_answer(raw)
        return ItemResult(
            id=rec["id"], condition=condition, gold=gold, predicted=pred,
            correct=answers_match(gold, pred), input_chars=len(q_used),
            input_tokens=n_tokens, token_method=method, latency_s=latency,
            raw_response=raw,
        )
    except Exception as exc:  # retries exhausted — record as errored, EXCLUDE later
        return ItemResult(
            id=rec["id"], condition=condition, gold=gold, predicted=None,
            correct=False, input_chars=len(q_used), input_tokens=n_tokens,
            token_method=method, latency_s=0.0, error=str(exc),
        )


def build_conditions(
    levels: tuple[str, ...], include_tier2: bool, baseline_repeat: bool = False,
) -> list[tuple[str, str | None, bool]]:
    conditions: list[tuple[str, str | None, bool]] = [("baseline", None, False)]
    if baseline_repeat:
        # Identical to baseline; run again to measure the pure decode-noise
        # floor psi_0 (temp=0 is not bit-deterministic on frontier backends).
        # Any treatment discordance below psi_0 is indistinguishable from noise.
        conditions.append(("baseline_repeat", None, False))
    for level in levels:
        conditions.append((level, level, False))
        if include_tier2:
            conditions.append((f"{level}+tier2", level, True))
    return conditions


def summarize_condition(results: list[ItemResult]) -> dict[str, Any]:
    n = len(results)
    k = sum(1 for r in results if r.correct)
    lo, hi = wilson_ci(k, n) if n else (0.0, 1.0)
    return {
        "n": n, "correct": k, "accuracy": (k / n if n else 0.0),
        "wilson_ci_95": [lo, hi],
        "mean_input_tokens": (sum(r.input_tokens for r in results) / n if n else 0.0),
        "n_errors": sum(1 for r in results if r.error),
        "n_unparseable": sum(1 for r in results if r.predicted is None and r.error is None),
    }


def compare_to_baseline(baseline: list[ItemResult], treatment: list[ItemResult]) -> dict[str, Any]:
    """Paired McNemar comparison. Items where EITHER arm errored (API failure,
    retries exhausted) are excluded from the b/c/both tally and counted under
    n_excluded_errors — otherwise a transient treatment-side failure would be
    miscounted as 'the tool broke the answer' and bias the delta negative
    (critic point 1)."""
    base_by_id = {r.id: r for r in baseline}
    b = c = both_correct = both_wrong = 0
    n_excluded_errors = 0
    for t in treatment:
        base = base_by_id.get(t.id)
        if base is None:
            continue
        if base.error is not None or t.error is not None:
            n_excluded_errors += 1
            continue
        if base.correct and not t.correct:
            b += 1
        elif not base.correct and t.correct:
            c += 1
        elif base.correct and t.correct:
            both_correct += 1
        else:
            both_wrong += 1
    n_valid = b + c + both_correct + both_wrong
    psi = (b + c) / n_valid if n_valid else 0.0
    mdd = min_detectable_d_mcnemar(psi, n_valid) if n_valid else None
    return {
        "n_valid_pairs": n_valid,
        "n_excluded_errors": n_excluded_errors,
        "baseline_correct_treatment_wrong": b,
        "baseline_wrong_treatment_correct": c,
        "both_correct": both_correct, "both_wrong": both_wrong,
        "answer_stability_delta": (c - b) / n_valid if n_valid else 0.0,
        "discordant_rate_psi": psi,
        "min_detectable_delta_at_this_n": mdd,
        "mcnemar_exact_p": mcnemar_exact_p(b, c),
    }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    records, source = load_gsm8k(cache_path=Path(args.data_cache), offline=args.offline)
    sample = select_sample(records, args.n, args.seed)
    print(f"[dataset] source={source} loaded={len(records)} sampled={len(sample)} seed={args.seed}")
    if source.startswith("fallback"):
        print("[dataset] WARNING: using the hand-authored offline fallback sample, "
              "NOT official GSM8K data. Any accuracy numbers below are a pipeline "
              "smoke test only, not a real benchmark result.")

    available, reason = api_available()

    levels = tuple(args.levels.split(",")) if args.levels else LEVELS
    print("\n=== Offline reduction diagnostics (no API calls) ===")
    diagnostics = {}
    for level in levels:
        d = run_reduction_diagnostics(sample, level, tier2=False)
        diagnostics[d.level] = d
        variants = [d]
        if args.include_tier2:
            if available:
                d2 = run_reduction_diagnostics(sample, level, tier2=True)
            else:
                # tier2=True with no key degrades to Tier-1 output unchanged
                # (see reduce_text); re-running it would just re-print the
                # same [tier2] stderr warning once per item for no new info.
                d2 = dataclass_replace(d, level=f"{level}+tier2")
            diagnostics[d2.level] = d2
            variants.append(d2)
        for dv in variants:
            note = "" if (available or "+tier2" not in dv.level) else "  (no API key: identical to tier1)"
            print(f"  {dv.level:>18}: changed {dv.n_changed}/{dv.n_items} "
                  f"({dv.n_changed / dv.n_items * 100 if dv.n_items else 0:.1f}%), "
                  f"mean char delta {dv.mean_char_delta_pct:.1f}%, "
                  f"numeric-fidelity violations {dv.n_numeric_fidelity_violations}, "
                  f">{TIER2_CHAR_THRESHOLD}ch (tier2-eligible) {dv.n_exceeds_tier2_threshold}{note}")
            if dv.n_numeric_fidelity_violations:
                print(f"    !! {dv.n_numeric_fidelity_violations} item(s) lost a number under reduction — see results JSON.")

    out: dict[str, Any] = {
        "dataset_source": source, "n_sampled": len(sample), "seed": args.seed,
        "model": args.model, "levels": levels,
        "reduction_diagnostics": {
            k: {kk: vv for kk, vv in vars(v).items() if kk not in ("examples", "violation_examples")}
            for k, v in diagnostics.items()
        },
    }

    if not available:
        print(f"\n=== SKIPPED: full accuracy run requires ANTHROPIC_API_KEY + anthropic SDK ===")
        print(f"  Reason: {reason}.")
        print("  Install with: pip install anthropic ; export ANTHROPIC_API_KEY=sk-...")
        out["accuracy_run"] = {"status": "skipped_no_api", "reason": reason}
        out["write_status"] = _write_out(args.out, out)
        return out

    import anthropic  # noqa: PLC0415
    client = anthropic.Anthropic()

    # (point 4) tier2 is a guaranteed no-op when no question exceeds the tier2
    # char threshold — running the +tier2 API conditions would double cost for
    # byte-identical inputs. Auto-skip them when the sample has zero eligible
    # items, and say so.
    tier2_eligible = sum(1 for rec in sample if len(rec["question"]) > TIER2_CHAR_THRESHOLD)
    effective_include_tier2 = args.include_tier2
    if args.include_tier2 and tier2_eligible == 0:
        effective_include_tier2 = False
        print(f"\n[tier2] 0/{len(sample)} questions exceed the {TIER2_CHAR_THRESHOLD}-char "
              "tier2 threshold, so +tier2 inputs are byte-identical to their base "
              "conditions. Auto-skipping the +tier2 API conditions (no info, double cost). "
              "Override with a larger --tier2-... corpus or a different dataset.")

    conditions = build_conditions(levels, effective_include_tier2, baseline_repeat=args.baseline_repeat)

    # (point 2) up-front power warning tied to this actual n.
    print(f"\n=== Running {len(conditions)} conditions x {len(sample)} questions "
          f"= {len(conditions) * len(sample)} API calls "
          f"(model={args.model}, interleave={args.interleave}) ===")
    print(f"[power] n={len(sample)}. As a rule of thumb (see --power-calc), detecting a "
          "3pp paired difference needs ~430-1740 pairs depending on the discordant rate; "
          "a 5pp difference needs ~155-630. If n is below these you may see a null result "
          "that reflects low power, not a safe tool. Achieved power is reported per "
          "condition below once the discordant rate is known.")

    run_started = datetime.now(timezone.utc).isoformat()
    all_results, timing = execute_conditions(
        client, args.model, sample, conditions,
        args.max_tokens, args.sleep, args.max_retries, args.interleave,
    )
    run_ended = datetime.now(timezone.utc).isoformat()

    baseline_results = all_results["baseline"]
    summary = {name: summarize_condition(rs) for name, rs in all_results.items()}
    comparisons = {
        name: compare_to_baseline(baseline_results, rs)
        for name, rs in all_results.items() if name != "baseline"
    }
    out["tier2_eligible_in_sample"] = tier2_eligible
    out["run_started_utc"] = run_started
    out["run_ended_utc"] = run_ended
    out["condition_timing_utc"] = timing
    out["accuracy_run"] = {
        "status": "completed",
        "summary": summary,
        "comparisons_vs_baseline": comparisons,
        "items": {
            name: [vars(r) for r in rs] for name, rs in all_results.items()
        },
    }
    print("\n=== Accuracy by condition ===")
    for name, s in summary.items():
        lo, hi = s["wilson_ci_95"]
        print(f"  {name:>18}: {s['accuracy']*100:5.1f}%  (95% CI [{lo*100:.1f}, {hi*100:.1f}])  "
              f"n={s['n']}  errors={s['n_errors']}  unparseable={s['n_unparseable']}")
    print("\n=== Paired comparison vs baseline (exact McNemar; errored items excluded) ===")
    for name, c in comparisons.items():
        mdd = c["min_detectable_delta_at_this_n"]
        mdd_str = f"min-detectable delta {mdd*100:.1f}pp" if mdd is not None else "min-detectable delta n/a (psi=0)"
        flag = ""
        if mdd is not None and abs(c["answer_stability_delta"]) < mdd and c["mcnemar_exact_p"] > 0.05:
            flag = "  [UNDERPOWERED: observed delta below what this n can resolve]"
        print(f"  {name:>18}: stability delta {c['answer_stability_delta']*100:+.1f}pp  "
              f"(b={c['baseline_correct_treatment_wrong']}, c={c['baseline_wrong_treatment_correct']}, "
              f"psi={c['discordant_rate_psi']:.3f}, excl_err={c['n_excluded_errors']})  "
              f"p={c['mcnemar_exact_p']:.4f}  {mdd_str}{flag}")
    if args.baseline_repeat:
        psi0 = comparisons.get("baseline_repeat", {}).get("discordant_rate_psi")
        if psi0 is not None:
            print(f"\n[noise floor] baseline-vs-baseline_repeat discordant rate psi_0={psi0:.3f} "
                  "(pure decode noise at temp=0). Treat any treatment psi at or below this as "
                  "indistinguishable from noise.")

    out["write_status"] = _write_out(args.out, out)
    return out


def execute_conditions(
    client: Any, model: str, sample: list[dict[str, Any]],
    conditions: list[tuple[str, str | None, bool]],
    max_tokens: int, sleep_s: float, max_retries: int, interleave: bool,
) -> tuple[dict[str, list[ItemResult]], dict[str, dict[str, str]]]:
    """Run all conditions. (point 8) Records per-condition UTC start/end and,
    when interleave=True, runs conditions in randomized order WITHIN each item so
    time-correlated infra drift (rate-limit ramps, latency spikes) is spread
    across conditions instead of hitting one condition's whole batch."""
    def stamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    all_results: dict[str, list[ItemResult]] = {name: [] for name, _, _ in conditions}
    timing: dict[str, dict[str, str]] = {name: {} for name, _, _ in conditions}

    if interleave:
        rng = random.Random(0)  # fixed => reproducible interleave order
        for rec in sample:
            order = conditions[:]
            rng.shuffle(order)
            for name, level, tier2 in order:
                timing[name].setdefault("start_utc", stamp())
                all_results[name].append(
                    _run_one(client, model, rec, name, level, tier2, max_tokens, max_retries)
                )
                timing[name]["end_utc"] = stamp()
                if sleep_s:
                    time.sleep(sleep_s)
        for name in all_results:  # stable order for pairing/output
            all_results[name].sort(key=lambda r: r.id)
    else:
        for name, level, tier2 in conditions:
            print(f"  running condition: {name} ...")
            timing[name]["start_utc"] = stamp()
            all_results[name] = run_condition(
                client, model, sample, name, level, tier2, max_tokens, sleep_s, max_retries,
            )
            timing[name]["end_utc"] = stamp()
    return all_results, timing


def _write_out(path: str | None, out: dict[str, Any]) -> int:
    """Write results JSON. Returns 0 on success (or when no path is given), 1 on
    failure. Never raises on I/O problems: the run's work is already printed to
    stdout, so a bad --out path must degrade to a clean one-line stderr error
    and a nonzero exit code, not a stack trace after all the work is done."""
    if not path:
        return 0
    p = Path(path)
    try:
        if p.parent and not p.parent.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    except OSError as exc:
        sys.stderr.write(f"[results] ERROR: could not write results to {path}: {exc}\n")
        return 1
    print(f"\n[results] written to {path}")
    return 0


# --------------------------------------------------------------------------- #
# --power-calc: print required-n tables. Fully offline.
# --------------------------------------------------------------------------- #
def print_power_calc() -> None:
    print("=== Unpaired two-proportion z-test: per-arm n required ===")
    print("(Conservative upper bound — ignores the positive correlation you get")
    print(" from running the SAME questions through baseline and treatment.)")
    print(f"{'p1':>6} {'p2':>6} {'swing(pp)':>10} {'n/arm (80% pow)':>16} {'n/arm (90% pow)':>16}")
    for p1, p2 in [(0.90, 0.85), (0.90, 0.87), (0.80, 0.75), (0.80, 0.77)]:
        n80 = required_n_two_proportion(p1, p2, power=0.8)
        n90 = required_n_two_proportion(p1, p2, power=0.9)
        print(f"{p1:>6.2f} {p2:>6.2f} {abs(p1-p2)*100:>10.1f} {n80:>16} {n90:>16}")
    print(f"\n(GSM8K test split has {EXPECTED_TEST_COUNT} questions total.)")

    print("\n=== Paired exact McNemar test: total-pairs n required (Connor 1987 approx) ===")
    print("psi = discordant-pair rate (unknown in advance); d = net accuracy delta")
    print(f"{'psi':>6} {'d(pp)':>8} {'n (80% pow)':>14} {'n (90% pow)':>14}")
    for psi in (0.05, 0.10, 0.15, 0.20):
        for d_pp in (3, 5):
            d = d_pp / 100
            if psi <= d * d:
                continue
            n80 = required_n_mcnemar(psi, d, power=0.8)
            n90 = required_n_mcnemar(psi, d, power=0.9)
            print(f"{psi:>6.2f} {d_pp:>8} {n80:>14} {n90:>14}")
    print(f"\n(GSM8K test split has {EXPECTED_TEST_COUNT} questions total; a paired run")
    print(" can use ALL of them as one set of pairs, unlike the unpaired design above")
    print(" which needs that many PER ARM.)")


# --------------------------------------------------------------------------- #
# Self-test: everything that can run with no network and no API key.
# --------------------------------------------------------------------------- #
def _assert(cond: bool, msg: str, failures: list[str]) -> None:
    if not cond:
        failures.append(msg)


def selftest() -> int:
    failures: list[str] = []

    # -- stats primitives -----------------------------------------------
    _assert(abs(_norm_ppf(0.975) - 1.959963985) < 1e-6, "_norm_ppf(0.975) wrong", failures)
    _assert(abs(_norm_ppf(0.8) - 0.8416212) < 1e-5, "_norm_ppf(0.8) wrong", failures)
    _assert(abs(_norm_ppf(0.9) - 1.2815516) < 1e-5, "_norm_ppf(0.9) wrong", failures)

    lo, hi = wilson_ci(100, 100)
    _assert(hi <= 1.0 and lo > 0.9, f"wilson_ci(100,100) unreasonable: {(lo, hi)}", failures)
    lo, hi = wilson_ci(0, 100)
    _assert(lo == 0.0 and hi < 0.1, f"wilson_ci(0,100) unreasonable: {(lo, hi)}", failures)
    lo, hi = wilson_ci(50, 100)
    _assert(lo < 0.5 < hi, f"wilson_ci(50,100) should straddle 0.5: {(lo, hi)}", failures)

    _assert(mcnemar_exact_p(5, 5) == 1.0, "mcnemar_exact_p(5,5) should be 1.0 (symmetric)", failures)
    _assert(mcnemar_exact_p(0, 10) < 0.01, "mcnemar_exact_p(0,10) should be tiny", failures)
    _assert(mcnemar_exact_p(0, 0) == 1.0, "mcnemar_exact_p(0,0) should be 1.0 (no evidence)", failures)

    n = required_n_two_proportion(0.90, 0.85, power=0.8)
    _assert(600 < n < 750, f"required_n_two_proportion(.90,.85) out of expected range: {n}", failures)
    n2 = required_n_two_proportion(0.90, 0.87, power=0.8)
    _assert(n2 > n, "smaller swing should require larger n", failures)

    # -- answer extraction -------------------------------------------------
    gold_cases = [
        ("Step 1...\n#### 72", 72.0),
        ("...#### 2,125", 2125.0),
        ("...#### -10", -10.0),
        ("...#### 3.5", 3.5),
        ("...#### 0", 0.0),
    ]
    for text, expected in gold_cases:
        got = extract_gold_answer(text)
        _assert(got == expected, f"extract_gold_answer({text!r}) = {got}, expected {expected}", failures)

    pred_cases = [
        ("Let's work through it. The answer is 72.", 72.0),
        ("Final Answer: $1,234.56", 1234.56),
        ("So we end up with -10 apples left. Answer: -10", -10.0),
        ("1) buy 2 apples 2) sell 3 => total profit is 5 dollars.", 5.0),
        ("No numbers here at all.", None),
        ("The discount is 20%. Answer: 20%", 20.0),
        ("Steps shown.\nAnswer: 42", 42.0),
        ("The final answer is 3.5 cups.", 3.5),
        ("She has 12 apples, then 18, then finally 6 remain.\nAnswer: 6", 6.0),
        ("", None),
        # (point 7) superseded earlier guess must NOT win over the corrected one.
        ("So the answer is 42 at first glance. Wait, rechecking: the true answer is actually 48, not 42.", 48.0),
        # (point 7) with a compliant final line, the last line is authoritative
        # even though an earlier wrong guess appears first.
        ("The answer is 42.\nWait, that's wrong.\nAnswer: 48", 48.0),
        # (point 10) unspaced subtraction on the weak fallback path: last number
        # is 13, and -5 must not be introduced as a spurious token.
        ("18-5=13 apples remain.", 13.0),
    ]
    for text, expected in pred_cases:
        got = extract_predicted_answer(text)
        ok = (got == expected) or (expected is not None and got is not None and abs(got - expected) < 1e-9)
        _assert(ok, f"extract_predicted_answer({text!r}) = {got}, expected {expected}", failures)

    # (point 10) direct check on the number regex: no spurious -5 from "18-5".
    _assert("-5.0" not in numbers_in("18-5=13"),
            f"_NUM_RE introduced a spurious negative from unspaced subtraction: {numbers_in('18-5=13')}", failures)
    _assert(numbers_in("18-5=13") == {"18.0", "5.0", "13.0"},
            f"numbers_in('18-5=13') wrong: {numbers_in('18-5=13')}", failures)
    # negative numbers after whitespace/start are still valid signs.
    _assert(extract_gold_answer("#### -10") == -10.0, "gold negative broke after regex change", failures)

    _assert(answers_match(3.0, 3) is True, "answers_match(3.0, 3) should be True", failures)
    _assert(answers_match(3.0, 4) is False, "answers_match(3.0, 4) should be False", failures)
    _assert(answers_match(None, 3.0) is False, "answers_match(None, x) should be False", failures)

    # (point 1) errored treatment items must be EXCLUDED from McNemar, not
    # counted as accuracy regressions. Reproduces the critic's scenario:
    # baseline 5/5 correct; treatment = 1 real-wrong + 1 API-error + 3 correct.
    base = [ItemResult(id=i, condition="baseline", gold=1.0, predicted=1.0, correct=True,
                       input_chars=0, input_tokens=0, token_method="x", latency_s=0.0)
            for i in range(5)]
    treat = [
        ItemResult(id=0, condition="t", gold=1.0, predicted=2.0, correct=False,
                   input_chars=0, input_tokens=0, token_method="x", latency_s=0.0),
        ItemResult(id=1, condition="t", gold=1.0, predicted=None, correct=False,
                   input_chars=0, input_tokens=0, token_method="x", latency_s=0.0, error="rate limit 429"),
        *[ItemResult(id=i, condition="t", gold=1.0, predicted=1.0, correct=True,
                     input_chars=0, input_tokens=0, token_method="x", latency_s=0.0) for i in range(2, 5)],
    ]
    cmp = compare_to_baseline(base, treat)
    _assert(cmp["baseline_correct_treatment_wrong"] == 1,
            f"errored item leaked into b: b={cmp['baseline_correct_treatment_wrong']} (expected 1)", failures)
    _assert(cmp["n_excluded_errors"] == 1,
            f"errored item not excluded: n_excluded_errors={cmp['n_excluded_errors']} (expected 1)", failures)
    _assert(cmp["n_valid_pairs"] == 4, f"n_valid_pairs wrong: {cmp['n_valid_pairs']} (expected 4)", failures)
    _assert(abs(cmp["answer_stability_delta"] - (-0.25)) < 1e-9,
            f"delta wrong after exclusion: {cmp['answer_stability_delta']} (expected -0.25)", failures)

    # (point 2) min-detectable-delta inverts required_n_mcnemar.
    _assert(required_n_mcnemar(0.10, 0.05) <= 320, "required_n_mcnemar sanity drifted", failures)
    mdd = min_detectable_d_mcnemar(0.10, required_n_mcnemar(0.10, 0.05))
    _assert(mdd is not None and abs(mdd - 0.05) < 0.004,
            f"min_detectable_d_mcnemar did not invert required_n_mcnemar: {mdd}", failures)
    _assert(min_detectable_d_mcnemar(0.0, 1000) is None, "psi=0 should yield None (nothing estimable)", failures)
    _assert(min_detectable_d_mcnemar(0.10, 10_000) < 0.05,
            "more pairs should detect a smaller delta", failures)

    # (points 4 & 6) condition construction.
    _assert(build_conditions(LEVELS, False) == [
        ("baseline", None, False), ("safe", "safe", False),
        ("balanced", "balanced", False), ("aggressive", "aggressive", False)],
        "build_conditions(no tier2) wrong", failures)
    _assert(len(build_conditions(LEVELS, True)) == 7, "build_conditions(+tier2) should be 7 conditions", failures)
    names_rep = [c[0] for c in build_conditions(LEVELS, False, baseline_repeat=True)]
    _assert(names_rep[:2] == ["baseline", "baseline_repeat"],
            f"baseline_repeat not inserted: {names_rep}", failures)

    # (point 3) pinned-dataset constants are well-formed.
    _assert(len(GSM8K_COMMIT) == 40 and all(ch in "0123456789abcdef" for ch in GSM8K_COMMIT),
            "GSM8K_COMMIT is not a 40-char hex sha", failures)
    _assert(len(GSM8K_SHA256) == 64, "GSM8K_SHA256 is not a 64-char hex digest", failures)
    _assert(GSM8K_COMMIT in GSM8K_URL and "master" not in GSM8K_URL, "URL not pinned to commit", failures)
    sample_bytes = b'{"question": "q1", "answer": "a\\n#### 3"}\n{"question": "q2", "answer": "b\\n#### 4"}\n'
    parsed = _parse_jsonl_bytes(sample_bytes)
    _assert(len(parsed) == 2 and parsed[0]["question"] == "q1", "_parse_jsonl_bytes round-trip failed", failures)

    # (orchestrator robustness fix) _write_out must never raise on I/O errors.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        # good path (incl. a not-yet-existing subdir) -> writes, returns 0
        good = Path(td) / "sub" / "results.json"
        try:
            rc_good = _write_out(str(good), {"ok": True})
        except Exception as exc:  # must not raise
            rc_good = -1
            _assert(False, f"_write_out raised on a good path: {exc!r}", failures)
        _assert(rc_good == 0 and good.exists(), f"_write_out(good) rc={rc_good}, exists={good.exists()}", failures)
        # bad path: a file used as a directory component -> OSError inside, must
        # be caught and reported as rc=1 with NO traceback escaping.
        blocker = Path(td) / "blocker"
        blocker.write_text("x", encoding="utf-8")
        bad = blocker / "cannot" / "results.json"  # blocker is a file, not a dir
        try:
            rc_bad = _write_out(str(bad), {"ok": True})
        except Exception as exc:  # THIS is the bug we are guarding against
            rc_bad = -1
            _assert(False, f"_write_out leaked a traceback on a bad path: {exc!r}", failures)
        _assert(rc_bad == 1, f"_write_out(bad) should return 1, got {rc_bad}", failures)
        # no path -> no-op success
        _assert(_write_out(None, {"ok": True}) == 0, "_write_out(None) should return 0", failures)

    # -- whole-document-JSON guard ------------------------------------------
    _assert(looks_like_whole_document_json('{"a": 1}') is True, "JSON guard should detect object", failures)
    _assert(looks_like_whole_document_json("[1, 2, 3]") is True, "JSON guard should detect array", failures)
    _assert(looks_like_whole_document_json("A robe takes 2 bolts...") is False, "JSON guard false-positived on prose", failures)
    _assert(looks_like_whole_document_json("{not valid json") is False, "JSON guard false-positived on unclosed brace", failures)

    # -- reduction invariance, part 1: the checker is non-vacuous -----------
    # Prove check_numeric_fidelity actually detects a dropped number by feeding
    # it a hand-made (original -> reduced-with-a-number-removed) pair. This does
    # NOT depend on reduce_text's behavior, so it stays a valid test of the
    # checker even as the tool improves.
    orig = "Sarah has 12 apples. Later she finds 18 more in the barn."
    hand_reduced = "Sarah has 12 apples."  # the '18' was dropped
    _assert(check_numeric_fidelity(orig, hand_reduced) == ["18.0"],
            f"checker failed to report a dropped number: {check_numeric_fidelity(orig, hand_reduced)}", failures)

    # -- reduction invariance, part 2: the safety guarantee HOLDS -----------
    # Two near-duplicate paragraphs differing ONLY in a key number. Earlier,
    # reduce_text's near-duplicate paragraph removal collapsed them and silently
    # dropped one number, contradicting its "numbers are never altered" claim.
    # remove_near_duplicates is now number-aware (guards on the numeric-literal
    # set), so BOTH numbers must survive at every level. This test is the
    # regression guard for that fix.
    p1 = "Sarah has 12 apples and gives away 5 to her friend Tom."
    p2 = "Sarah has 18 apples and gives away 5 to her friend Tom."
    adversarial = p1 + "\n\n" + p2
    for level in LEVELS:
        red = reduce_text(adversarial, level=level)
        violations = check_numeric_fidelity(adversarial, red)
        _assert(not violations,
                f"reduce_text dropped a number under near-dup removal at level={level}: {violations} "
                "(number-aware near-dup guard regressed)", failures)

    # -- reduction invariance: benign case must NOT false-positive ----------
    benign = "James runs 3 sprints of 60 meters each, three times a week."
    for level in LEVELS:
        red = reduce_text(benign, level=level)
        violations = check_numeric_fidelity(benign, red)
        _assert(not violations, f"false-positive numeric-fidelity violation at level={level}: {violations}", failures)

    # -- tier2 gating: no API key present -> tier2=True must equal tier1 ----
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sample_q = "A robe takes 2 bolts of blue fiber and half that much white fiber. How many bolts in total?"
        t1 = reduce_text(sample_q, level="balanced", tier2=False)
        t2 = reduce_text(sample_q, level="balanced", tier2=True)
        _assert(t1 == t2, "tier2=True with no ANTHROPIC_API_KEY should degrade to Tier-1-only output unchanged", failures)

    # -- dataset loading (offline fallback path only, no network) -----------
    records, source = load_gsm8k(offline=True)
    _assert(source.startswith("fallback"), "offline=True should use the fallback sample", failures)
    _assert(len(records) == len(FALLBACK_SAMPLE), "fallback sample size mismatch", failures)
    for rec in records:
        g = extract_gold_answer(rec["answer"])
        _assert(g is not None, f"fallback record has unparseable gold answer: {rec}", failures)

    sample = select_sample(records, n=5, seed=DEFAULT_SEED)
    _assert(len(sample) == min(5, len(records)), "select_sample returned wrong count", failures)
    sample2 = select_sample(records, n=5, seed=DEFAULT_SEED)
    _assert([r["id"] for r in sample] == [r["id"] for r in sample2], "select_sample is not deterministic for a fixed seed", failures)

    # -- reduction diagnostics run end-to-end on the fallback sample --------
    diag = run_reduction_diagnostics(records_to_sample(records), "balanced")
    _assert(diag.n_items == len(records), "diagnostics n_items mismatch", failures)

    # -- report -------------------------------------------------------------
    print(f"selftest: {len(failures)} failure(s) out of many checks")
    for f in failures:
        print(f"  FAIL: {f}")
    if not failures:
        print("selftest: ALL CHECKS PASSED (offline-only; API-backed accuracy run was NOT exercised)")
    return 1 if failures else 0


def records_to_sample(records: list[dict[str, str]]) -> list[dict[str, Any]]:
    return [{"id": i, "question": r["question"], "answer": r["answer"]} for i, r in enumerate(records)]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--selftest", action="store_true", help="run offline self-checks and exit")
    parser.add_argument("--power-calc", action="store_true", help="print sample-size/power tables and exit")
    parser.add_argument("--n", type=int, default=DEFAULT_N, help=f"sample size (default {DEFAULT_N}; GSM8K test has {EXPECTED_TEST_COUNT})")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="random seed for sample selection (reproducible)")
    parser.add_argument("--levels", default=",".join(LEVELS), help="comma-separated levels to test")
    parser.add_argument("--include-tier2", action="store_true", default=True, help="also run each level with tier2=True (default on; GSM8K questions are short so this is usually a no-op — see diagnostics)")
    parser.add_argument("--no-tier2", dest="include_tier2", action="store_false")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Claude model id to use for the accuracy run")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES, help="transient-error retries per item before it is excluded")
    parser.add_argument("--sleep", type=float, default=0.0, help="seconds to sleep between API calls")
    parser.add_argument("--baseline-repeat", action="store_true", help="run baseline twice to estimate the decode-noise floor psi_0 (point 6)")
    parser.add_argument("--interleave", action="store_true", help="run conditions in randomized order within each item to decorrelate infra drift (point 8)")
    parser.add_argument("--data-cache", default=str(DATA_CACHE))
    parser.add_argument("--offline", action="store_true", help="skip network entirely; use the bundled fallback sample")
    parser.add_argument("--out", default=None, help="path to write full JSON results")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.power_calc:
        print_power_calc()
        return 0

    out = run_benchmark(args)
    return int(out.get("write_status", 0))  # nonzero if results couldn't be written


if __name__ == "__main__":
    raise SystemExit(main())
