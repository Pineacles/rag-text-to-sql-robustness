"""Build report/report.md and its PNG charts from every result file in results/ (any machine).

Usage: uv run python -m tools.report   (run.py also calls this after every finished experiment)
"""

import argparse
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # write PNG files, no window
import matplotlib.pyplot as plt  # noqa: E402

import config  # noqa: E402
import experiments  # noqa: E402
from common import prompt  # noqa: E402

DIFFICULTIES = ["simple", "moderate", "challenging"]
ERROR_TYPES = ["correct", "wrong_result", "sql_error", "timeout", "no_sql", "gold_error"]
DATASET_DESCRIPTIONS = {
    "arcwise_plat": "Arcwise-Plat (Jin et al. 2026): BIRD Mini-Dev with corrected SQL, questions and evidence, 498 "
    "questions, 11 databases. Difficulty taken from BIRD Dev.",
    "bird_dev": "BIRD Dev (Li et al. 2023), original annotations, 1,534 questions, 11 databases.",
}
MAX_EXAMPLES_SHOWN = 5
# Beskorovainyi 2026, Table 1: Qwen2.5-Coder-Instruct "base" (one greedy call, BIRD evidence in the prompt, fp16 on
# vLLM), full BIRD Dev.
LITERATURE_BIRD_DEV = {"qwen2.5-coder:7b": 39.05, "qwen2.5-coder:14b": 47.39}
LITERATURE_RESCORED = config.RESULTS_DIR / "measurements" / "beskorovainyi_rescored.json"  # compare_literature.py


# --- Loading --------------------------------------------------------------------------------------------
def load_runs(results_dir):
    """Every experiment that has a result file: its meta data and its rows."""
    runs = []
    for meta_path in sorted(results_dir.glob("*.meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        rows_path = meta_path.with_name(meta_path.name.replace(".meta.json", ".jsonl"))
        rows = []
        if rows_path.exists():
            rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        meta["complete"] = len(rows) >= meta["n_total"]
        runs.append({"meta": meta, "rows": rows, "exp": meta["experiment"]})
    runs.sort(key=run_sort_key)
    return runs


def run_sort_key(run):
    exp = run["exp"]
    model_order = list(config.MODELS).index(exp["model"]) if exp["model"] in config.MODELS else 99
    return (exp["stage"], exp["dataset"] != "arcwise_plat", model_order, exp["use_evidence"],
            exp["contamination"], exp["contamination_seed"] or 0, run["meta"]["machine"])


def label(run):
    """Short human-readable name of a run for tables and charts."""
    exp = run["exp"]
    text = f"{exp['model']} hint-{'on' if exp['use_evidence'] else 'off'} s{exp['stage']}"
    if exp["stage"] == 3:
        text += f" c{round(exp['contamination'] * 100)}% seed{exp['contamination_seed']}"
    if exp["stage"] == 4:
        text += f" {exp['attack']} n{exp['limit']}"
    if run["meta"]["machine"] != config.MACHINE:
        text += f" [{run['meta']['machine']}]"
    return text


# --- Small helpers --------------------------------------------------------------------------------------
def accuracy(rows):
    """Execution accuracy in percent (None for no rows)."""
    return 100 * sum(row["correct"] for row in rows) / len(rows) if rows else None


def percent(value):
    return "–" if value is None else f"{value:.1f}"


def status(run):
    meta = run["meta"]
    if meta["complete"]:
        return "complete"
    return f"**running {len(run['rows'])}/{meta['n_total']}**" if not meta["finished"] else "incomplete"


def table(header, rows):
    """A Markdown table from a header list and a list of row lists."""
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def one_line(sql):
    return " ".join(str(sql).split()).replace("|", "\\|")


def same_sql(a, b):
    """Same query text after normalising whitespace, case and a trailing ;."""
    return one_line(a).rstrip(";").strip().lower() == one_line(b).rstrip(";").strip().lower()


def same_setup(run, **fields):
    return all(run["exp"][key] == value for key, value in fields.items())


# --- Sections -------------------------------------------------------------------------------------------
def setup_section(runs):
    """What was run, on what, and how it was scored; the report must explain itself."""
    models = {}
    for run in runs:
        meta = run["meta"]
        details = meta.get("model_details", {})
        models[(meta["experiment"]["model"], meta["machine"])] = [
            meta["experiment"]["model"], meta["weights"], details.get("quantization_label", "?"),
            (details.get("digest") or "?")[:12], meta.get("ollama_version", "?"), meta["machine"], meta["hardware"],
        ]
    datasets = sorted({run["exp"]["dataset"] for run in runs})
    options = config.GENERATION_OPTIONS
    example_question = {"question": "{question}", "evidence": "{evidence}", "db_id": "california_schools"}
    template = prompt.build_prompt(example_question, use_evidence=True, examples=[])
    template = template.split("## Database schema and information:")[0] + "## Database schema and information:\n" \
        "{CREATE TABLE statements of the database}\n## Task Output:\nSQL Query:"
    lines = [
        "## Setup",
        "",
        table(["Model", "Weights", "Ollama quant label", "Digest", "Ollama", "Machine", "Hardware"], models.values()),
        "",
        # Unsloth dynamic quants mix tensor types per layer; Ollama's label names only one of them.
        "The \"Weights\" column is authoritative. Ollama's quant label for a mixed-precision file (Unsloth UD) "
        "names only one tensor type, so it can differ from the file name.",
        "",
        "**Datasets:** " + " ".join(DATASET_DESCRIPTIONS[d] for d in datasets),
        "",
        f"**Generation:** temperature {options['temperature']}, seed {options['seed']}, num_ctx {options['num_ctx']}, "
        f"max {options['num_predict']} output tokens, thinking off, one call per question (no refine step).",
        "",
        "**Scoring:** execution accuracy (EX) as in BIRD: the predicted and the gold query are executed on the SQLite "
        "database and the prediction is correct when both return the same *set* of rows. SQL timeout "
        f"{config.SQL_TIMEOUT_SECONDS} s. Error types: correct, wrong_result, sql_error (query crashed), timeout, "
        "no_sql (no SQL in the answer).",
        "",
        "**Prompt** (Cook et al. 2025, App. B.1, NP-0 Generate SQL Prompt). The `## Evidence` part is only present "
        "with hint-on. Stage 2 to 4 insert Cook's `Examples` block (top-3 retrieved question/SQL pairs) after the "
        "first instruction line.",
        "",
        "```text\n" + template + "\n```",
    ]
    return "\n".join(lines)


def status_section(runs):
    rows = []
    for run in runs:
        gpu = run["meta"].get("gpu", {}).get("vram_share")
        gpu_text = "–" if gpu is None else ("100 %" if gpu >= 0.999 else f"**{gpu:.0%} (spilled to CPU)**")
        rows.append([label(run), run["exp"]["dataset"], f"{len(run['rows'])}/{run['meta']['n_total']}",
                     status(run), gpu_text, run["meta"]["updated"]])
    return "## Run status\n\n" + table(["Experiment", "Dataset", "Done", "Status", "On GPU", "Last update"], rows)


def accuracy_section(runs, dataset, chart_dir):
    """EX per experiment as table and bar chart."""
    selected = [run for run in runs if run["exp"]["dataset"] == dataset]
    rows = [[label(run), len(run["rows"]), sum(r["correct"] for r in run["rows"]), percent(accuracy(run["rows"])),
             status(run)] for run in selected]
    chart = bar_chart(chart_dir / f"accuracy_{dataset}.png", [label(run) for run in selected],
                      [accuracy(run["rows"]) or 0 for run in selected], f"Execution accuracy, {dataset}")
    return ("### Execution accuracy\n\n" + table(["Experiment", "n", "Correct", "EX %", "Status"], rows)
            + f"\n\n![accuracy]({chart})")


def difficulty_section(runs, chart_dir, dataset):
    selected = [run for run in runs if run["exp"]["dataset"] == dataset]
    rows = []
    values = {difficulty: [] for difficulty in DIFFICULTIES}
    for run in selected:
        cells = [label(run)]
        for difficulty in DIFFICULTIES:
            subset = [r for r in run["rows"] if r["difficulty"] == difficulty]
            cells.append(f"{percent(accuracy(subset))} (n={len(subset)})")
            values[difficulty].append(accuracy(subset) or 0)
        rows.append(cells)
    chart = grouped_bar_chart(chart_dir / f"difficulty_{dataset}.png", [label(run) for run in selected], values,
                              f"EX by difficulty, {dataset}")
    return ("### Accuracy by difficulty\n\n" + table(["Experiment", *DIFFICULTIES], rows)
            + f"\n\n![difficulty]({chart})")


def database_section(runs, dataset):
    selected = [run for run in runs if run["exp"]["dataset"] == dataset]
    databases = sorted({r["db_id"] for run in selected for r in run["rows"]})
    rows = []
    for database in databases:
        cells = [database]
        for run in selected:
            subset = [r for r in run["rows"] if r["db_id"] == database]
            cells.append(f"{percent(accuracy(subset))} ({len(subset)})")
        rows.append(cells)
    return "### Accuracy by database (EX %, n)\n\n" + table(["Database", *[label(run) for run in selected]], rows)


def hint_section(runs, dataset):
    """Hint on vs off per model, counted only on questions both runs have finished (fair comparison)."""
    rows = []
    for off_run in [r for r in runs if same_setup(r, dataset=dataset, stage=1, use_evidence=False)]:
        on_run = find_run(runs, off_run, use_evidence=True)
        if not on_run:
            continue
        off_rows, on_rows = paired_rows(off_run, on_run)
        gained = sum(1 for a, b in zip(off_rows, on_rows, strict=True) if not a["correct"] and b["correct"])
        lost = sum(1 for a, b in zip(off_rows, on_rows, strict=True) if a["correct"] and not b["correct"])
        off_accuracy, on_accuracy = accuracy(off_rows), accuracy(on_rows)
        rows.append([off_run["exp"]["model"], len(off_rows), percent(off_accuracy), percent(on_accuracy),
                     f"{on_accuracy - off_accuracy:+.1f}", gained, lost])
    if not rows:
        return ""
    header = ["Model", "n (both done)", "EX hint-off", "EX hint-on", "Δ pp", "only with hint", "only without hint"]
    return "### Hint on vs off (Stage 1)\n\n" + table(header, rows)


def error_section(runs, dataset, chart_dir, chart_name=None):
    selected = [run for run in runs if run["exp"]["dataset"] == dataset]
    chart_name = chart_name or f"errors_{dataset}"
    rows = []
    shares = {error_type: [] for error_type in ERROR_TYPES}
    for run in selected:
        counts = Counter(r["error_type"] for r in run["rows"])
        rows.append([label(run), *[counts.get(error_type, 0) for error_type in ERROR_TYPES]])
        for error_type in ERROR_TYPES:
            shares[error_type].append(100 * counts.get(error_type, 0) / max(len(run["rows"]), 1))
    chart = stacked_bar_chart(chart_dir / f"{chart_name}.png", [label(run) for run in selected], shares,
                              f"Error types (% of questions), {dataset}")
    return "### Error types\n\n" + table(["Experiment", *ERROR_TYPES], rows) + f"\n\n![errors]({chart})"


def latency_section(runs, dataset):
    rows = []
    for run in [r for r in runs if r["exp"]["dataset"] == dataset]:
        seconds = [r["seconds"] for r in run["rows"]]
        tokens = [r["prompt_tokens"] or 0 for r in run["rows"]]
        if not seconds:
            continue
        rows.append([label(run), f"{statistics.mean(seconds):.1f}", f"{statistics.median(seconds):.1f}",
                     f"{max(seconds):.1f}", round(statistics.mean(tokens)), max(tokens), run["meta"]["machine"]])
    header = ["Experiment", "mean s", "median s", "max s", "mean prompt tokens", "max prompt tokens", "Machine"]
    note = (f"Seconds = model generation time per question (Ollama request), without SQL execution. "
            f"A max prompt close to num_ctx ({config.GENERATION_OPTIONS['num_ctx']}) would mean truncation.")
    return "### Latency\n\n" + note + "\n\n" + table(header, rows)


def examples_section(runs, dataset):
    """Questions every Stage 1 run got wrong, and questions where the hint made the difference."""
    stage1 = [run for run in runs if same_setup(run, dataset=dataset, stage=1)]
    if not stage1:
        return ""
    rows_by_question = defaultdict(list)
    for run in stage1:
        for row in run["rows"]:
            rows_by_question[row["question_id"]].append(row)
    all_wrong = [rows for rows in rows_by_question.values() if len(rows) == len(stage1)
                 and not any(r["correct"] for r in rows)]
    parts = [f"### Examples\n\n**Questions every Stage 1 run got wrong:** {len(all_wrong)} of "
             f"{sum(1 for rows in rows_by_question.values() if len(rows) == len(stage1))} answered by all runs."]
    for rows in all_wrong[:MAX_EXAMPLES_SHOWN]:
        parts.append(example_block(rows[0], rows[-1], f"{label(stage1[-1])} predicted"))
    for off_run in [r for r in stage1 if not r["exp"]["use_evidence"]]:
        on_run = find_run(runs, off_run, use_evidence=True)
        if not on_run:
            continue
        off_rows, on_rows = paired_rows(off_run, on_run)
        flips = [(a, b) for a, b in zip(off_rows, on_rows, strict=True) if not a["correct"] and b["correct"]]
        parts.append(f"**Hint made the difference ({off_run['exp']['model']}):** {len(flips)} questions wrong "
                     f"without the hint and correct with it.")
        for off_row, _ in flips[:MAX_EXAMPLES_SHOWN]:
            parts.append(example_block(off_row, off_row, "predicted without hint"))
    return "\n\n".join(parts)


def example_block(row, predicted_row, predicted_label):
    return (f"- `{row['question_id']}` {row['db_id']}, {row['difficulty']}: {row['question']}\n"
            f"  - gold: `{one_line(row['gold_sql'])}`\n"
            f"  - {predicted_label}: `{one_line(predicted_row['predicted_sql']) or '(no SQL)'}` "
            f"→ {predicted_row['error_type']}")


def retrieval_section(runs):
    """Stage 1 vs Stage 2 on the same questions: does retrieval help?"""
    rows = []
    for stage2 in [r for r in runs if same_setup(r, stage=2)]:
        stage1 = find_run(runs, stage2, stage=1)
        if not stage1:
            continue
        rows1, rows2 = paired_rows(stage1, stage2)
        similarities = [e["similarity"] for r in rows2 for e in r["retrieved_examples"]]
        rows.append([stage2["exp"]["model"], "on" if stage2["exp"]["use_evidence"] else "off", len(rows1),
                     percent(accuracy(rows1)), percent(accuracy(rows2)), f"{accuracy(rows2) - accuracy(rows1):+.1f}",
                     f"{statistics.mean(similarities):.3f}" if similarities else "–", status(stage2)])
    if not rows:
        return ""
    header = ["Model", "Hint", "n (both done)", "EX Stage 1", "EX Stage 2", "Δ pp", "mean similarity", "Stage 2"]
    return ("## Stage 2: retrieval memory (top-3 clean examples)\n\n" + table(header, rows)
            + "\n\nMemory: BIRD Dev questions outside the test set with their gold SQL; retrieval within the same "
            f"database by cosine similarity of {config.EMBEDDING_MODEL} question embeddings.")


def contamination_groups(runs):
    """Stage 2 (level 0) and Stage 3 runs grouped as {(model, hint, machine): {level: [runs over seeds]}}."""
    groups = defaultdict(dict)
    for run in runs:
        if run["exp"]["stage"] in (2, 3) and run["exp"]["dataset"] == "arcwise_plat":
            key = (run["exp"]["model"], run["exp"]["use_evidence"], run["meta"]["machine"])
            level = run["exp"]["contamination"] if run["exp"]["stage"] == 3 else 0.0
            groups[key].setdefault(level, []).append(run)
    return groups


def level_row(name, level, level_runs):
    """One table row for one contamination level: mean and spread of EX over the seeds."""
    values = [accuracy(run["rows"]) for run in level_runs if run["rows"]]
    complete = sum(run["meta"]["complete"] for run in level_runs)
    spread = statistics.stdev(values) if len(values) > 1 else 0.0
    share = contaminated_share([r for run in level_runs for r in run["rows"]])
    mean = statistics.mean(values) if values else None
    row = [name, f"{level:.0%}", percent(mean), f"{spread:.1f}",
           f"{min(values):.1f}–{max(values):.1f}" if values else "–", f"{complete}/{len(level_runs)}", f"{share:.1%}"]
    point = (level * 100, mean, spread) if values else None
    return row, point


def contamination_section(runs, chart_dir):
    """Accuracy vs contamination level, mean and spread over the seeds; level 0 is the clean Stage 2 run."""
    rows, series = [], {}
    for (model, use_evidence, machine), levels in contamination_groups(runs).items():
        if len(levels) < 2:
            continue
        name = f"{model} hint-{'on' if use_evidence else 'off'}"
        if machine != config.MACHINE:
            name += f" [{machine}]"
        series[name] = []
        for level in sorted(levels):
            row, point = level_row(name, level, levels[level])
            rows.append(row)
            if point:
                series[name].append(point)
    if not rows:
        return ""
    chart = line_chart(chart_dir / "contamination.png", series)
    header = ["Model", "Contamination", "mean EX %", "std (seeds)", "min–max", "seeds complete",
              "wrong share among retrieved"]
    return ("## Stage 3: contaminated memory\n\n" + table(header, rows) + f"\n\n![contamination]({chart})\n\n"
            "Contamination = share of the memory pool whose SQL was replaced by an executable but semantically wrong "
            "version (rule-based near-miss). The last column is how many of the examples actually "
            "shown to the model were wrong.")


def contaminated_share(rows):
    examples = [e for r in rows for e in r["retrieved_examples"]]
    return sum(e["contaminated"] for e in examples) / len(examples) if examples else 0.0


def affected_questions_section(runs):
    """Paired view: questions that were shown at least one wrong example, clean memory vs contaminated memory.

    Overall EX dilutes the effect because most questions never see a wrong example. Here the same questions are
    compared between the clean Stage 2 run and the Stage 3 run, so the difference is caused by the wrong examples.
    Seeds are pooled: n counts question/seed pairs.
    """
    pooled = defaultdict(lambda: {"clean": 0, "contaminated": 0, "n": 0})
    for run in [r for r in runs if r["exp"]["stage"] == 3]:
        clean_run = find_run(runs, run, stage=2)
        if not clean_run:
            continue
        contaminated_rows, clean_rows = paired_rows(run, clean_run)
        key = (run["exp"]["model"], run["exp"]["use_evidence"], run["exp"]["contamination"])
        for contaminated_row, clean_row in zip(contaminated_rows, clean_rows, strict=True):
            if any(e["contaminated"] for e in contaminated_row["retrieved_examples"]):
                pooled[key]["n"] += 1
                pooled[key]["clean"] += clean_row["correct"]
                pooled[key]["contaminated"] += contaminated_row["correct"]
    rows = []
    for (model, use_evidence, level), counts in sorted(pooled.items()):
        clean = 100 * counts["clean"] / counts["n"]
        contaminated = 100 * counts["contaminated"] / counts["n"]
        rows.append([model, "on" if use_evidence else "off", f"{level:.0%}", counts["n"], f"{clean:.1f}",
                     f"{contaminated:.1f}", f"{contaminated - clean:+.1f}"])
    if not rows:
        return ""
    header = ["Model", "Hint", "Contamination", "n affected (question×seed)", "EX clean memory",
              "EX contaminated memory", "Δ pp"]
    return ("### Questions that saw at least one wrong example\n\n" + table(header, rows)
            + "\n\nSame questions, same model: only the memory differs (clean Stage 2 vs Stage 3).")


def clean_reference(runs, run):
    """The clean Stage 2 run with the same model, hint, dataset and machine as an attack run."""
    exp = run["exp"]
    return next((r for r in runs if r["exp"]["stage"] == 2 and r["exp"]["model"] == exp["model"]
                 and r["exp"]["use_evidence"] == exp["use_evidence"] and r["exp"]["dataset"] == exp["dataset"]
                 and r["meta"]["machine"] == run["meta"]["machine"]), None)


def attack_section(runs):
    """Stage 4: each worst-case attack against the clean Stage 2 memory, on exactly the same questions."""
    rows = []
    for run in [r for r in runs if r["exp"]["stage"] == 4]:
        exp = run["exp"]
        clean = clean_reference(runs, run)
        if not clean:
            continue
        attacked, clean_rows = paired_rows(run, clean)
        if not attacked:
            continue
        shown = [e for row in attacked for e in row["retrieved_examples"]]
        wrong = [e for e in shown if e["contaminated"]]
        hit = [row for row in attacked if any(e["contaminated"] for e in row["retrieved_examples"])]
        copied = sum(1 for row in attacked if any(e["contaminated"] and same_sql(e["sql"], row["predicted_sql"])
                                                   for e in row["retrieved_examples"]))
        ex_clean, ex_attack = accuracy(clean_rows), accuracy(attacked)
        rows.append([exp["model"], "on" if exp["use_evidence"] else "off", exp["attack"], len(attacked),
                     percent(ex_clean), percent(ex_attack), f"{ex_attack - ex_clean:+.1f}",
                     f"{100 * len(wrong) / len(shown):.0f}%" if shown else "–", len(hit), copied, status(run)])
    if not rows:
        return ""
    return ("## Stage 4: worst-case contamination (small batch)\n\n" + table(
        ["Model", "Hint", "Attack", "n", "EX clean memory", "EX attacked", "Δ pp", "wrong share shown",
         "questions with a wrong example", "answers = a wrong example", "status"], rows)
        + "\n\nSame questions, same model: only the examples differ. Attacks are explained in "
        "`stages/stage4_attacks.py`: "
        "top1 = most similar example wrong, all3 = all three wrong, poison = the test question itself planted "
        "with a wrong SQL; rule = Stage 3 near-miss, schema = a column or join key swapped, schema-x2 = two swaps, "
        "swap = the gold SQL of another question, poison-all3 = planted and both other examples wrong, "
        "wrong-db = the three most similar examples from other databases.")


def poison_section(runs):
    """Poison runs on the questions that really got a planted example: copied, repaired to gold, or else."""
    rows = []
    for run in [r for r in runs if r["exp"]["stage"] == 4 and r["exp"]["attack"].startswith("poison")]:
        clean = clean_reference(runs, run)
        if not clean:
            continue
        pairs = [(a, c) for a, c in zip(*paired_rows(run, clean), strict=True)
                 if str(a["retrieved_examples"][0]["question_id"]).startswith("poison-")]
        if not pairs:
            continue
        n = len(pairs)
        copied = sum(same_sql(a["retrieved_examples"][0]["sql"], a["predicted_sql"]) for a, _ in pairs)
        repaired = sum(same_sql(a["gold_sql"], a["predicted_sql"]) for a, _ in pairs)
        exp = run["exp"]
        rows.append([exp["model"], "on" if exp["use_evidence"] else "off", exp["attack"], n,
                     f"{copied} ({100 * copied / n:.0f}%)", f"{repaired} ({100 * repaired / n:.0f}%)",
                     percent(accuracy([c for _, c in pairs])), percent(accuracy([a for a, _ in pairs]))])
    if not rows:
        return ""
    return ("### Poison: copy or repair\n\n" + table(
        ["Model", "Hint", "Attack", "poisoned questions", "answer = planted SQL", "answer = gold SQL (text)",
         "EX clean memory", "EX poisoned"], rows)
        + "\n\nOnly questions that really got a planted example. Text comparison after normalising whitespace, "
        "case and `;`. \"= gold SQL\" counts a word-for-word repair; EX also counts other correct rewrites.")


def literature_section(runs):
    """Stage 1 on full BIRD Dev next to Beskorovainyi 2026, Table 1 (same models, fp16 on vLLM)."""
    rescored = json.loads(LITERATURE_RESCORED.read_text(encoding="utf-8")) if LITERATURE_RESCORED.exists() else {}
    rows = []
    for model, theirs in LITERATURE_BIRD_DEV.items():
        ours = {hint: next((r for r in runs if same_setup(r, model=model, dataset="bird_dev", stage=1,
                                                         use_evidence=hint)
                            and r["meta"]["machine"] == config.MACHINE), None) for hint in (False, True)}
        if not any(ours.values()):
            continue
        ex = {hint: accuracy(run["rows"]) if run else None for hint, run in ours.items()}
        mark = {hint: "" if not run or run["meta"]["complete"] else " (running)" for hint, run in ours.items()}
        again = rescored.get(model, {}).get("ex_rescored")
        gap = "–" if ex[True] is None else f"{ex[True] - theirs:+.1f}"
        if ex[True] is not None and again is not None:
            gap += f" / {ex[True] - again:+.1f}"
        rows.append([model, percent(ex[False]) + mark[False], percent(ex[True]) + mark[True], f"{theirs:.2f}",
                     percent(again), gap])
    if not rows:
        return ""
    return ("## Literature comparison: full BIRD Dev (Beskorovainyi 2026, Table 1)\n\n" + table(
        ["Model", "ours hint off", "ours hint on", "Beskorovainyi base (with evidence)",
         "their answers, our scorer", "ours hint on vs published / vs rescored"], rows)
        + "\n\nTheir \"base\" puts the BIRD evidence into the prompt, so our hint-on run is the fair comparison. "
        "Differences: fp16 on vLLM vs Q4_K_M on Ollama, their short prompt with a names-only schema vs NP-0 with "
        "the full CREATE TABLE statements, and the scorer (their released answers rescored with our scorer and "
        "the same gold SQL, column 5).")


def find_run(runs, base, **changes):
    """The run that has the same setup as base except for the given fields (same machine)."""
    wanted = {**base["exp"], **changes}
    if changes.get("stage") in (1, 2):
        wanted.update({"contamination": 0.0, "contamination_seed": None})
    for run in runs:
        if run["exp"] == wanted and run["meta"]["machine"] == base["meta"]["machine"]:
            return run
    return None


def paired_rows(run_a, run_b):
    """Rows of two runs restricted to questions both have finished, in the same order."""
    rows_b = {r["question_id"]: r for r in run_b["rows"]}
    pairs = [(r, rows_b[r["question_id"]]) for r in run_a["rows"] if r["question_id"] in rows_b]
    return [a for a, _ in pairs], [b for _, b in pairs]


# --- Charts ---------------------------------------------------------------------------------------------
def save(figure, path):
    figure.tight_layout()
    figure.savefig(path, dpi=120)
    plt.close(figure)
    return path.name


def bar_chart(path, labels, values, title):
    figure, axis = plt.subplots(figsize=(max(6, len(labels) * 0.9), 4.5))
    bars = axis.bar(range(len(labels)), values, color="#4C72B0")
    axis.bar_label(bars, fmt="%.1f")
    axis.set_ylim(0, 100)
    axis.set_xticks(range(len(labels)), labels, rotation=30, ha="right", fontsize=8)
    axis.set_ylabel("EX %")
    axis.set_title(title)
    return save(figure, path)


def grouped_bar_chart(path, labels, values_by_group, title):
    figure, axis = plt.subplots(figsize=(max(6, len(labels) * 1.2), 4.5))
    width = 0.8 / len(values_by_group)
    for index, (group, values) in enumerate(values_by_group.items()):
        axis.bar([x + index * width for x in range(len(labels))], values, width, label=group)
    axis.set_xticks([x + 0.4 - width / 2 for x in range(len(labels))], labels, rotation=30, ha="right", fontsize=8)
    axis.set_ylabel("EX %")
    axis.set_ylim(0, 115)  # room for the legend above the bars
    axis.set_title(title)
    axis.legend(ncol=3, loc="upper center")
    return save(figure, path)


def stacked_bar_chart(path, labels, shares_by_type, title):
    figure, axis = plt.subplots(figsize=(max(6, len(labels) * 0.9), 4.5))
    bottom = [0.0] * len(labels)
    for error_type, shares in shares_by_type.items():
        if any(shares):
            axis.bar(range(len(labels)), shares, bottom=bottom, label=error_type)
            bottom = [b + s for b, s in zip(bottom, shares, strict=True)]
    axis.set_xticks(range(len(labels)), labels, rotation=30, ha="right", fontsize=8)
    axis.set_ylabel("% of questions")
    axis.set_title(title)
    axis.legend(fontsize=8)
    return save(figure, path)


def line_chart(path, series):
    figure, axis = plt.subplots(figsize=(7, 4.5))
    for name, points in series.items():
        if points:
            levels, means, spreads = zip(*points, strict=True)
            axis.errorbar(levels, means, yerr=spreads, marker="o", capsize=4, label=name)
    axis.set_xticks([0] + [round(level * 100) for level in experiments.CONTAMINATION_LEVELS])
    axis.set_xlabel("contaminated share of the memory pool (%)")
    axis.set_ylabel("EX % (mean ± std over seeds)")
    axis.set_title("Accuracy vs contamination")
    axis.legend(fontsize=8)
    return save(figure, path)


# --- Report ---------------------------------------------------------------------------------------------
def stage1_sections(runs, dataset, chart_dir):
    title = {"arcwise_plat": "Stage 1: zero-shot baseline, Arcwise-Plat (verified, 498)",
             "bird_dev": "Stage 1: zero-shot baseline, full BIRD Dev (1,534)"}[dataset]
    selected = [run for run in runs if same_setup(run, dataset=dataset, stage=1)]
    if not selected:
        return ""
    sections = [f"## {title}", accuracy_section(selected, dataset, chart_dir),
                hint_section(selected, dataset), difficulty_section(selected, chart_dir, dataset),
                database_section(selected, dataset), error_section(selected, dataset, chart_dir),
                latency_section(selected, dataset), examples_section(selected, dataset)]
    return "\n\n".join(section for section in sections if section)


def build_report(results_dir=config.RESULTS_DIR, report_dir=config.REPORT_DIR):
    """Write report.md and the charts; called after every finished experiment."""
    report_dir.mkdir(parents=True, exist_ok=True)
    runs = load_runs(results_dir)
    stage23 = [run for run in runs if run["exp"]["stage"] in (2, 3)]  # Stage 4 has its own summary
    sections = [
        "# BAA results: Robust Retrieval-Augmented Text-to-SQL with Locally Deployable Open-Weight Models",
        f"Generated {datetime.now():%Y-%m-%d %H:%M} from `{results_dir.name}/` ({len(runs)} experiments). "
        "Rows marked **running** are partial: their numbers cover only the questions finished so far.",
        setup_section(runs),
        status_section(runs),
        stage1_sections(runs, "arcwise_plat", report_dir),
        stage1_sections(runs, "bird_dev", report_dir),
        literature_section(runs),
        retrieval_section(runs),
        "## Stage 2/3 error types\n\n" + error_section(stage23, "arcwise_plat", report_dir, "errors_stage23")
        if stage23 else "",
        contamination_section(runs, report_dir),
        affected_questions_section(runs),
        attack_section(runs),
        poison_section(runs),
    ]
    text = "\n\n".join(section for section in sections if section) + "\n"
    (report_dir / "report.md").write_text(text, encoding="utf-8")
    print(f"report written: {report_dir / 'report.md'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=config.RESULTS_DIR)
    parser.add_argument("--report-dir", type=Path, default=config.REPORT_DIR)
    args = parser.parse_args()
    build_report(args.results_dir, args.report_dir)


if __name__ == "__main__":
    main()
