#!/usr/bin/env python3
"""ISAS Token Reducer — code mode.

Reduces the token size of SOURCE CODE that you pass to Claude as *context*.
It never rewrites your real files — it produces a leaner copy for the model.

What it removes (all safe for a context copy, all configurable):
  1. Comments        — language-aware. Preserves shebangs, encoding lines, and
     directive comments (noqa, type:, pragma, eslint-disable, @ts-ignore,
     go:build, clippy/allow, SPDX/license, …) so behavior-affecting comments
     survive.
  2. Blank lines     — collapse runs to one (default) or remove entirely.
  3. Trailing whitespace / tab normalization.
  Optional, off by default:
  4. Docstrings (Python, via ast).
  5. Skeleton mode   — keep signatures/declarations, drop function bodies
     (Python). Biggest reduction; changes what info is present (structure only).

What it NEVER touches: string contents, numbers, identifiers, logic, or any
executable token. Comment detection is tokenizer/scanner based, so a `#` or
`//` inside a string is left alone.

Importable:
    from reduce_code import reduce_code
    lean = reduce_code(src, lang="python")

CLI:
    python reduce_code.py app.py --stats
    python reduce_code.py app.js --blank-lines remove --stats
    python reduce_code.py app.py --strip-docstrings --stats
    python reduce_code.py app.py --skeleton --stats
"""
from __future__ import annotations

import argparse
import ast
import io
import re
import sys
import tokenize
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from count_tokens import count_tokens, is_estimate  # noqa: E402

# --------------------------------------------------------------------------- #
# Directive detection — comments we KEEP because they affect tooling/behavior.
# --------------------------------------------------------------------------- #
_DIRECTIVE_KEYWORDS = (
    "noqa", "type:", "pragma", "pylint", "pyright", "mypy", "flake8", "ruff",
    "isort", "fmt:", "yapf", "nosec", "coding:", "coding=", "eslint", "tslint",
    "ts-ignore", "ts-expect-error", "ts-nocheck", "prettier", "stylelint",
    "istanbul", "c8 ", "v8 ", "jshint", "jslint", "biome-ignore", "go:build",
    "go:generate", "go:embed", "clippy", "allow(", "deny(", "warn(", "rustfmt",
    "deno-lint", "sonar", "checkstyle", "spotless", "license", "copyright",
    "spdx", "todo", "fixme", "safety:", "nolint",
)


def _is_directive(comment_text: str) -> bool:
    t = comment_text.strip()
    if t.startswith("#!"):  # shebang
        return True
    low = t.lower()
    return any(k in low for k in _DIRECTIVE_KEYWORDS)


# --------------------------------------------------------------------------- #
# Python
# --------------------------------------------------------------------------- #
def _python_docstring_lines(src: str) -> set[int]:
    """1-indexed line numbers that belong to docstrings (module/class/def)."""
    lines: set[int] = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return lines
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(getattr(body[0], "value", None), ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                d = body[0]
                start = getattr(d, "lineno", None)
                end = getattr(d, "end_lineno", start)
                if start:
                    lines.update(range(start, (end or start) + 1))
    return lines


def strip_python(src: str, keep_directives: bool = True, strip_docstrings: bool = False) -> str:
    """Remove Python comments (and optionally docstrings) safely via tokenize/ast."""
    cut: dict[int, int] = {}  # line -> column to truncate at (comment start)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                if keep_directives and _is_directive(tok.string):
                    continue
                row, col = tok.start
                if row not in cut or col < cut[row]:
                    cut[row] = col
    except (tokenize.TokenError, IndentationError):
        # Source doesn't tokenize cleanly — do not risk a bad cut.
        return src

    drop_lines = _python_docstring_lines(src) if strip_docstrings else set()

    out: list[str] = []
    for i, line in enumerate(src.split("\n"), start=1):
        if i in drop_lines:
            continue
        if i in cut:
            line = line[: cut[i]].rstrip()
            if line == "":  # was a standalone comment line
                continue
        out.append(line)
    return "\n".join(out)


def skeleton_python(src: str) -> str:
    """Keep imports, signatures, and class/def headers; drop function bodies.

    Bodies become `...`. Docstrings are dropped. This is the biggest reduction
    and preserves the API surface / structure, not the implementation.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    src_lines = src.split("\n")

    def header_lines(node) -> list[str]:
        # decorators + the def/class header up to and including the colon line
        start = min([d.lineno for d in getattr(node, "decorator_list", [])] + [node.lineno])
        # find the line where the signature's colon closes: first body stmt lineno - 1
        body = node.body
        end = body[0].lineno - 1 if body else node.lineno
        return src_lines[start - 1 : end]

    pieces: list[str] = []

    def indent_of(line: str) -> str:
        return line[: len(line) - len(line.lstrip())]

    def walk(node, container_indent=""):
        for child in getattr(node, "body", []):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                hdr = header_lines(child)
                pieces.extend(hdr)
                pad = indent_of(hdr[-1]) if hdr else container_indent
                pieces.append(pad + "    ...")
            elif isinstance(child, ast.ClassDef):
                hdr = header_lines(child)
                pieces.extend(hdr)
                walk(child, indent_of(hdr[-1]) if hdr else container_indent)
            elif isinstance(child, (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign)):
                seg = src_lines[child.lineno - 1 : getattr(child, "end_lineno", child.lineno)]
                pieces.extend(seg)
    walk(tree)
    return strip_python("\n".join(pieces), keep_directives=True)


# --------------------------------------------------------------------------- #
# C-family scanner ( // and /* */ ), string-aware
# --------------------------------------------------------------------------- #
def strip_c_family(src: str, keep_directives: bool = True) -> str:
    i, n = 0, len(src)
    out: list[str] = []
    while i < n:
        c = src[i]
        if c in ('"', "'", "`"):  # string / char / template literal
            quote = c
            out.append(c)
            i += 1
            while i < n:
                d = src[i]
                out.append(d)
                if d == "\\" and i + 1 < n:
                    out.append(src[i + 1])
                    i += 2
                    continue
                i += 1
                if d == quote:
                    break
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":  # line comment
            j = i + 2
            while j < n and src[j] != "\n":
                j += 1
            comment = src[i:j]
            if keep_directives and _is_directive(comment):
                out.append(comment)
            i = j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":  # block comment
            j = i + 2
            while j < n - 1 and not (src[j] == "*" and src[j + 1] == "/"):
                j += 1
            j = min(j + 2, n)
            comment = src[i:j]
            if keep_directives and _is_directive(comment):
                out.append(comment)
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)


# --------------------------------------------------------------------------- #
# Hash-comment scanner ( # ), string-aware — shell, ruby, yaml, toml, …
# --------------------------------------------------------------------------- #
def strip_hash(src: str, keep_directives: bool = True) -> str:
    out_lines: list[str] = []
    for row, line in enumerate(src.split("\n"), start=1):
        in_s = in_d = False
        cut = None
        k = 0
        while k < len(line):
            ch = line[k]
            if ch == "\\" and (in_s or in_d):
                k += 2
                continue
            if ch == "'" and not in_d:
                in_s = not in_s
            elif ch == '"' and not in_s:
                in_d = not in_d
            elif ch == "#" and not in_s and not in_d:
                cut = k
                break
            k += 1
        if cut is not None:
            comment = line[cut:]
            if not (keep_directives and (_is_directive(comment) or (row == 1 and comment.startswith("#!")))):
                truncated = line[:cut].rstrip()
                if truncated == "":
                    continue
                line = truncated
        out_lines.append(line)
    return "\n".join(out_lines)


# --------------------------------------------------------------------------- #
# Dispatch + tidy
# --------------------------------------------------------------------------- #
_C_FAMILY_EXT = {
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".c", ".h", ".cpp", ".hpp",
    ".cc", ".hh", ".cs", ".java", ".go", ".rs", ".swift", ".kt", ".kts",
    ".scala", ".php", ".m", ".mm", ".dart", ".proto",
}
_PY_EXT = {".py", ".pyi"}
_HASH_EXT = {".sh", ".bash", ".zsh", ".rb", ".yml", ".yaml", ".toml", ".pl", ".r"}


def detect_lang(path: str | None) -> str:
    if not path:
        return "unknown"
    ext = Path(path).suffix.lower()
    if ext in _PY_EXT:
        return "python"
    if ext in _C_FAMILY_EXT:
        return "c"
    if ext in _HASH_EXT:
        return "hash"
    return "unknown"


def tidy_lines(src: str, blank_mode: str = "collapse") -> str:
    lines = [ln.replace("\t", "    ").rstrip() for ln in src.split("\n")]
    if blank_mode == "remove":
        lines = [ln for ln in lines if ln.strip() != ""]
    elif blank_mode == "collapse":
        collapsed: list[str] = []
        prev_blank = False
        for ln in lines:
            blank = ln.strip() == ""
            if blank and prev_blank:
                continue
            collapsed.append(ln)
            prev_blank = blank
        lines = collapsed
    while lines and lines[0].strip() == "":
        lines.pop(0)
    while lines and lines[-1].strip() == "":
        lines.pop()
    return "\n".join(lines)


def reduce_code(
    src: str,
    *,
    lang: str = "unknown",
    keep_comments: bool = False,
    keep_directives: bool = True,
    strip_docstrings: bool = False,
    skeleton: bool = False,
    blank_mode: str = "collapse",
) -> str:
    """Reduce source code for use as context. See module docstring."""
    if skeleton and lang == "python":
        return tidy_lines(skeleton_python(src), blank_mode)
    if not keep_comments:
        if lang == "python":
            src = strip_python(src, keep_directives, strip_docstrings)
        elif lang == "c":
            src = strip_c_family(src, keep_directives)
        elif lang == "hash":
            src = strip_hash(src, keep_directives)
        # unknown: leave comments, just tidy whitespace
    return tidy_lines(src, blank_mode)


def _format_stats(before: str, after: str) -> str:
    b, method = count_tokens(before)
    a, _ = count_tokens(after)
    saved = b - a
    pct = (saved / b * 100) if b else 0.0
    est = " (estimated)" if is_estimate(method) else ""
    return (
        f"[code-stats]{est} tokens: {b} -> {a} (saved {saved}, {pct:.1f}%)  "
        f"lines: {before.count(chr(10)) + 1} -> {after.count(chr(10)) + 1}  [{method}]"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Reduce source-code tokens for context (safe copy).")
    p.add_argument("input", nargs="?", help="source file; omit to read stdin (needs --lang)")
    p.add_argument("-o", "--output", help="write result to a file instead of stdout")
    p.add_argument("--stats", action="store_true", help="print before/after token stats to stderr")
    p.add_argument("--lang", default="auto", choices=["auto", "python", "c", "hash", "unknown"],
                   help="language family (default: auto-detect by extension)")
    p.add_argument("--keep-comments", action="store_true", help="do not strip comments")
    p.add_argument("--no-keep-directives", action="store_true",
                   help="also strip directive comments (noqa, eslint, etc.) — riskier")
    p.add_argument("--strip-docstrings", action="store_true", help="Python: also remove docstrings")
    p.add_argument("--skeleton", action="store_true", help="Python: keep signatures, drop bodies")
    p.add_argument("--blank-lines", default="collapse", choices=["collapse", "remove", "keep"])
    args = p.parse_args(argv)

    if args.input:
        with open(args.input, encoding="utf-8") as fh:
            original = fh.read()
    else:
        original = sys.stdin.read()

    lang = detect_lang(args.input) if args.lang == "auto" else args.lang
    if lang == "unknown" and args.lang == "auto":
        sys.stderr.write("[code] unknown language; stripping whitespace only (pass --lang to strip comments).\n")

    reduced = reduce_code(
        original,
        lang=lang,
        keep_comments=args.keep_comments,
        keep_directives=not args.no_keep_directives,
        strip_docstrings=args.strip_docstrings,
        skeleton=args.skeleton,
        blank_mode=args.blank_lines,
    )

    if args.stats:
        sys.stderr.write(_format_stats(original, reduced) + "\n")
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(reduced + "\n")
    else:
        sys.stdout.write(reduced + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
