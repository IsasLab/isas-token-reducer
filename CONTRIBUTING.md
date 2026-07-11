# Contributing

## Quick start

```bash
git clone https://github.com/IsasLab/isas-token-reducer
cd isas-token-reducer
pip install pytest
python -m pytest tests/ -v
```

## Rules

- Tier 1 stays offline and dependency-free. Don't add a required import
  outside the Python standard library to `scripts/reduce.py`.
- Any change to `remove_exact_duplicates`, `remove_near_duplicates`,
  `trim_filler`, or `compress_phrases` needs a passing test in
  `tests/test_safety.py` proving numbers, code, and quotes are untouched.
- Small, focused PRs over large ones.

## Reporting a bug

Open an issue with: the input text (or a minimal reproduction), the exact
command you ran, and expected vs. actual output.

## Feature ideas

Open an issue before a large PR — saves both of us time if the direction
doesn't fit the project's safety-first scope.
