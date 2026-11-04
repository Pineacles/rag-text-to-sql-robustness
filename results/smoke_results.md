# BIRD Dev smoke test, 14.10.2026

Setup: `bird_smoke_test.py --n 20`, Cook NP-0 "Generate SQL Prompt" (App. B.1), empty examples, single call,
temperature 0, seed 42, num_ctx 8192, execution accuracy (row-set match), RTX 4080 16 GB, one run per config.
Questions: BIRD Dev 0–19, all `california_schools` (17 simple, 3 moderate). n=20 shows the pipeline works end to end; not a result.

## Summary

| Model | Evidence | Correct | EX | Simple (17) | Moderate (3) | s/question |
|---|---|---|---|---|---|---|
| qwen2.5-coder:7b | no | 3/20 | 15% | 3 | 0 | 4.0 |
| qwen2.5-coder:7b | yes | 5/20 | 25% | 5 | 0 | 3.0 |
| baa-qwen3.8-27b | no | 10/20 | 50% | 10 | 0 | 12.3 |
| baa-qwen3.8-27b | yes | 13/20 | 65% | 11 | 2 | 11.1 |
| *Cook NP-0 Initial (GPT-4o, all 11 DBs, 3-run avg)* | *no* | – | *35.1%* | – | – | – |

## Per question (✓ = correct)

| # | Difficulty | 7B no-ev | 7B ev | 27B no-ev | 27B ev |
|---|---|---|---|---|---|
| 0 | simple | ✓ | ✓ | ✓ | ✓ |
| 1 | moderate | | | | |
| 2 | simple | | | | ✓ |
| 3 | simple | | | ✓ | ✓ |
| 4 | moderate | | | | ✓ |
| 5 | simple | | | | ✓ |
| 6 | simple | | ✓ | ✓ | ✓ |
| 7 | simple | ✓ | ✓ | ✓ | ✓ |
| 8 | simple | ✓ | ✓ | ✓ | ✓ |
| 9 | simple | | | ✓ | ✓ |
| 10 | simple | | | ✓ | |
| 11 | simple | | | | ✓ |
| 12 | moderate | | | | ✓ |
| 13 | simple | | ✓ | ✓ | |
| 14 | simple | | | ✓ | ✓ |
| 15 | simple | | | | |
| 16 | simple | | | | |
| 17 | simple | | | | |
| 18 | simple | | | | |
| 19 | simple | | | ✓ | ✓ |

## Notes

- Evidence helps both models (+2 / +3), but costs 27B questions 10 and 13.
- 27B solves 2–3x more than 7B at about 3x the latency.
- Questions 1 and 15–18 fail everywhere: check gold SQL for annotation errors (Jin et al.) before blaming the model.
- Raw per-question logs of this first test are not included.
