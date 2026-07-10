#!/usr/bin/env python3
"""Classify a task as "small" (handle directly) or "large" (route as a
map-reduce workflow with cheap gathering subagents feeding a capable model).

Heuristic signals:
  * estimated raw-context tokens if everything were naively stuffed into one
    call (measured via count_tokens over the provided text/description),
  * number of files a refactor touches,
  * number of sources/searches a research task needs.

Thresholds are configurable. Sensible defaults: > 15,000 raw tokens, OR > 8
files, OR > 6 sources marks a task "large".

Importable:
    from classify_task import classify
    result = classify(raw_text=desc, files=12)

CLI:
    python classify_task.py --files 12
    python classify_task.py brief.txt --sources 7 --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from count_tokens import count_tokens  # noqa: E402

DEFAULT_TOKEN_THRESHOLD = 15_000
DEFAULT_FILE_THRESHOLD = 8
DEFAULT_SOURCE_THRESHOLD = 5


def classify(
    raw_text: str | None = None,
    *,
    files: int = 0,
    sources: int = 0,
    raw_tokens: int | None = None,
    token_threshold: int = DEFAULT_TOKEN_THRESHOLD,
    file_threshold: int = DEFAULT_FILE_THRESHOLD,
    source_threshold: int = DEFAULT_SOURCE_THRESHOLD,
) -> dict:
    """Return a classification dict with size, reasons, route, and agents."""
    token_method = None
    if raw_tokens is None and raw_text is not None:
        raw_tokens, token_method = count_tokens(raw_text)
    raw_tokens = raw_tokens or 0

    reasons: list[str] = []
    if raw_tokens > token_threshold:
        reasons.append(f"raw context ~{raw_tokens} tok > {token_threshold} threshold")
    if files > file_threshold:
        reasons.append(f"{files} files > {file_threshold} threshold")
    if sources > source_threshold:
        reasons.append(f"{sources} sources > {source_threshold} threshold")

    size = "large" if reasons else "small"

    # Decide which kind of large task this looks like (refactor vs research).
    kind = None
    agents: list[str] = []
    if size == "large":
        if files > file_threshold or (files and not sources):
            kind = "refactor"
            agents = ["context-scout", "implementer"]
        elif sources > source_threshold or sources:
            kind = "research"
            agents = ["research-gatherer", "synthesizer"]
        else:  # large purely by token volume, direction unknown
            kind = "unknown-large"
            agents = ["context-scout / research-gatherer (pick by task type)"]

    if size == "small":
        route = "single-pass: handle directly, optionally run reduce.py on pasted context"
    else:
        route = (
            "workflow-routing: gather+condense in isolated subagent contexts first, "
            "then feed only the condensed summary to the executing/synthesizing model"
        )

    return {
        "size": size,
        "kind": kind,
        "reasons": reasons or ["below all thresholds"],
        "raw_tokens": raw_tokens,
        "token_method": token_method,
        "recommended_route": route,
        "suggested_agents": agents,
        "thresholds": {
            "tokens": token_threshold,
            "files": file_threshold,
            "sources": source_threshold,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Classify a task as small or large.")
    parser.add_argument("input", nargs="?", help="optional file with the task brief / raw context")
    parser.add_argument("--files", type=int, default=0, help="number of files a refactor touches")
    parser.add_argument("--sources", type=int, default=0, help="number of sources a research task needs")
    parser.add_argument("--token-threshold", type=int, default=DEFAULT_TOKEN_THRESHOLD)
    parser.add_argument("--file-threshold", type=int, default=DEFAULT_FILE_THRESHOLD)
    parser.add_argument("--source-threshold", type=int, default=DEFAULT_SOURCE_THRESHOLD)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    raw_text = None
    if args.input:
        with open(args.input, encoding="utf-8") as fh:
            raw_text = fh.read()

    result = classify(
        raw_text=raw_text,
        files=args.files,
        sources=args.sources,
        token_threshold=args.token_threshold,
        file_threshold=args.file_threshold,
        source_threshold=args.source_threshold,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"size: {result['size'].upper()}  (kind: {result['kind']})")
        print("reasons:")
        for r in result["reasons"]:
            print(f"  - {r}")
        print(f"route: {result['recommended_route']}")
        if result["suggested_agents"]:
            print(f"suggested agents: {', '.join(result['suggested_agents'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
