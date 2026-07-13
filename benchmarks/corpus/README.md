# Honest compression corpus

A deliberately **mixed** set of samples for the Tier-1 before/after benchmark
(`benchmarks/compression_corpus.py`). Most published before/after numbers in this
repo were measured on *deliberately redundant* fixtures, which flatters the tool.
This corpus exists to keep the reporting honest: it puts genuinely-unique prose
next to redundant prose so the report shows **both** the high-redundancy wins
**and** the ~1–3% information-theory floor that unique text hits — the central
"HARD TRUTH" of this project: *no lossless offline pass can shrink text that has
no redundancy.*

The category of each sample is encoded in its filename prefix and read by the
runner:

| Prefix | Category | What it demonstrates |
|--------|----------|----------------------|
| `redundant_` | redundant | Duplicated paragraphs, repeated chat answers, filler/verbose phrasing — Tier-1's home turf. Expect large, honest savings. |
| `unique_` | unique | Dense, non-repetitive prose with little or no filler. Expect near the information-theory floor (~1–3%); this is correct, not a failure. |
| `mixed_` | mixed | Realistic material: mostly unique content with a little embedded redundancy. Savings land between the two extremes. |

## Samples

- `redundant_01_repeated_chat.txt` — a support chat where the same answer is
  pasted three times, plus "it is important to note that" / "basically" filler.
- `redundant_02_duplicate_paragraphs.txt` — one paragraph repeated verbatim three
  times followed by a distinct paragraph (exact-duplicate removal).
- `redundant_03_filler_and_verbose.txt` — packed with filler phrases and verbose
  constructions ("in order to", "due to the fact that", …) that the phrase map
  and filler list collapse.
- `unique_01_dense_technical.txt` — a technical explanation of TCP congestion
  control. Information-dense, essentially no redundancy.
- `unique_02_narrative_prose.txt` — original narrative prose; no repetition, no
  filler, nothing structural to remove.
- `unique_03_spec_with_facts.txt` — a dense API spec carrying numbers, names,
  code spans (`/v1/charge`), and a verbatim quote. Unique prose that also
  exercises the safety guarantees (nothing here may be altered).
- `mixed_01_meeting_notes.txt` — meeting notes: unique decisions and figures with
  one duplicated decision line and a couple of filler phrases.

## Honesty notes

- All token counts come from the shared real tokenizer in
  `scripts/count_tokens.py`. When `tiktoken` is not installed the counter falls
  back to a clearly-labelled `words*1.3` **estimate**; the runner surfaces the
  method so no absolute number is presented as more precise than it is. The
  *percentage* is computed same-method-both-sides and is the reliable figure.
- These measure **Tier-1 deterministic** reduction only. The lossy semantic tier
  is not a before/after percentage — it costs tokens to run and is reported as a
  net ledger elsewhere.
- Add your own samples by dropping a `<category>_NN_<slug>.txt` file here; the
  runner picks them up automatically.
