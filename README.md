# Robust Retrieval-Augmented Text-to-SQL with Locally Deployable Open-Weight Models

Code of a BSc thesis in Computer Science at HSLU (2026/27). The question it answers: when a local
open-weight model writes SQL with the help of solved examples from a memory, **what happens when some of those
examples are wrong?**

This is the public version of the thesis code. Development happened in a private repository.

Benchmark: BIRD Dev, tested on the 498 verified questions of Arcwise-Plat (Jin et al. 2026).
Models (all local, through Ollama): `qwen2.5-coder:7b` and Qwen3.8-27B in every stage; `qwen2.5-coder:14b` in
Stage 1 only, for the comparison with published results (Beskorovainyi 2026).
Method baseline: Cook et al. 2025 (NP-0 prompt, top-3 retrieved question/SQL pairs).

## The four stages

Every stage uses the same prompt, model call, SQL extraction and scoring. **A stage only decides which examples
the model sees**, and each stage is one file in `stages/`. Every stage runs with the BIRD hint (evidence) off and
on, and is scored with BIRD execution accuracy (EX): the predicted SQL is correct if it returns the same set of
rows as the gold SQL.

### Stage 1: zero-shot baseline (`stages/stage1_zero_shot.py`)

The model gets the question, the database schema and, with hint on, the BIRD evidence. No examples. This is the
reference point for everything else. It runs on the 498 verified Arcwise-Plat questions and on the full BIRD Dev
(1,534 questions), the latter for the comparison with published results.

### Stage 2: retrieval memory (`stages/stage2_retrieval.py`)

The memory holds the 1,036 Dev questions that are not test questions, each with its gold SQL. For every test
question the three most similar memory entries of the same database (bge-m3 embeddings, cosine similarity) are
added to the prompt as solved examples. The memory is clean here, apart from the known annotation errors in BIRD's
own gold SQL.

### Stage 3: contaminated memory (`stages/stage3_contamination.py`)

Same retrieval as Stage 2, but before it a share of the memory (5, 10 or 20 %) gets wrong SQL. A wrong SQL is a
near miss of the correct one: it still runs and returns rows, but a different result (for example `ASC` instead
of `DESC`, `MIN` instead of `MAX`, a flipped comparison). Which entries are wrong is chosen with a seed; every level
runs with seeds 1, 2 and 3, and the levels are nested (the 10 % set contains the 5 % set).

### Stage 4: worst-case attacks (`stages/stage4_attacks.py`)

Stage 3 spreads the wrong entries at random, so a test question often sees no wrong example at all. Stage 4 attacks
what is shown directly, on the first 100 verified questions:

| Attack | What the model sees |
|---|---|
| `top1-rule`, `top1-schema` | the most similar example is wrong |
| `all3-rule`, `all3-schema` | all three examples are wrong |
| `poison-rule`, `poison-schema` | the test question itself is planted in the memory with a wrong SQL |
| `poison-schema-x2` | as `poison-schema`, with two changes instead of one |
| `poison-all3` | the planted test question plus two wrong examples |
| `poison-swap` | the test question planted with the gold SQL of another question |
| `wrong-db` | the three most similar examples from the other databases, with correct SQL |

`rule` is the Stage 3 near miss, `schema` swaps a column or join key. Some attacks only apply where a valid wrong
version exists; on the other questions the run is plain Stage 2.

## Repository layout

```
run.py                 runs experiments (resumable, one JSONL line per question)
experiments.py         the list of every experiment; adding a run = adding a line
config.py              settings: paths, models, generation options
stages/                one file per stage (see above)
common/
  bird.py              load questions and schemas, execute SQL, judge a prediction (BIRD execution accuracy)
  llm.py               Ollama calls: generate, embed, GPU check
  prompt.py            the prompt template (Cook et al. NP-0) and the SQL extraction
tools/
  show_prompt.py       show exactly what the model saw for one question, wrong examples diffed
  verify_examples.py   recheck every stored run: right examples shown, wrong ones really wrong
  compare_runs.py      compare two result files question by question (reruns, machines)
  bird_smoke_test.py   the first standalone test (14.10.2026); Stage 1 is checked against it
  report.py            build report/report.md and the charts from results/
  compare_literature.py  rescore Beskorovainyi 2026's answers with our scorer (14B comparison)
  measure_speed.py     embedding and generation latency
models/                Ollama Modelfile for Qwen3.8-27B
results/               every result file (*.jsonl) with its metadata (*.meta.json); measurements/ holds the
                       speed tests and the rescored Beskorovainyi answers
report/                the generated report
```

## Checking a result yourself

Each result line stores the examples that went into the prompt. To see one prompt in full, with every wrong
example next to its correct SQL:

```bash
uv run python -m tools.show_prompt results/server-rtx5060ti__arcwise_plat__qwen2.5-coder-7b__hint-off__stage3__contam20__seed1.jsonl --no-schema
```

To recheck all runs:

```bash
uv run python -m tools.verify_examples --truth-only   # wrong examples really differ, clean ones equal (no model needed)
uv run python -m tools.verify_examples                # also recompute every example (needs Ollama for bge-m3)
```

## Running it

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com), a 16 GB GPU, and the
data below.

```bash
ollama pull qwen2.5-coder:7b && ollama pull bge-m3
```

The 27B model is built from a local GGUF file: point `FROM` in `models/Modelfile.baa-qwen3.8-27b` at the file,
then `ollama create baa-qwen3.8-27b -f models/Modelfile.baa-qwen3.8-27b`.

`run.py` works through every experiment in `experiments.py`. `--only TEXT` runs the experiments whose name
contains `TEXT`. Names look like `arcwise_plat__qwen2.5-coder-7b__hint-off__stage2`.

```bash
uv run run.py                                                    # everything, top to bottom
uv run run.py --only stage1                                      # Stage 1, all models, hints and datasets
uv run run.py --only qwen2.5-coder-7b__hint-off__stage2          # Stage 2, 7B, hint off
uv run run.py --only qwen2.5-coder-7b__hint-off__stage3__contam20__seed1   # one Stage 3 level and seed
uv run run.py --only stage3                                      # all of Stage 3
uv run run.py --only qwen2.5-coder-7b__hint-off__stage4__wrong-db         # one Stage 4 attack
uv run run.py --only stage4                                      # all of Stage 4
```

For a quick test add `--limit 10 --results-dir results_test --no-report`. `--limit` also overrides the
100-question limit of Stage 4, but the file keeps the name `...__n100`, so always use a separate `--results-dir`
for it. To compare a test run with a stored one:

```bash
uv run python -m tools.compare_runs results/RUN.jsonl results_test/RUN.jsonl
```

Runs are resumable: questions already in a result file are skipped. Generation is greedy (temperature 0, seed 42),
so the prompt is always the same, but on a GPU a few answers can still differ between two runs. `compare_runs`
fails on a prompt difference and only reports answer differences. Set `BAA_MACHINE` on another machine; its name
goes into every result file name, so results of different machines are never mixed.

## Data

Not included (license). Put it under `data/`:

- BIRD Dev: <https://bird-bench.oss-cn-beijing.aliyuncs.com/dev.zip>, unzipped to `data/bird/dev_20240627/`
  (including `dev_databases.zip`)
- Arcwise-Plat: `arcwise_plat_full_with_diff.json` from
  [uiuc-kang-lab/text_to_sql_benchmarks](https://github.com/uiuc-kang-lab/text_to_sql_benchmarks), to
  `data/arcwise/`

## References

- Cook et al. 2025, retrieval memory for text-to-SQL (NP-0 prompt): primary method baseline
- Beskorovainyi 2026, *How Far Do On-Prem Open LLMs Get on Text-to-SQL?*, arXiv:2606.29733: 14B comparison
- Gao et al. 2024 (NeurIPS), noisy in-context demonstrations: basis of the contamination design
- Jin et al. 2026, BIRD annotation errors and the Arcwise-Plat corrections
- Lin et al. 2025, knowledge-base poisoning: basis of the `poison` attacks
- Li et al. 2023, BIRD benchmark
