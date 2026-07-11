## What this changes

## Safety check
- [ ] `pytest tests/` passes locally
- [ ] If this touches `remove_exact_duplicates`, `remove_near_duplicates`, `trim_filler`, or `compress_phrases`: added/updated a case in `tests/test_safety.py`
- [ ] No new required dependency in the Tier 1 path (`scripts/reduce.py` core must stay offline, stdlib-only)
