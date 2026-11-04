"""Run the experiments from experiments.py. Per question: pick examples (stage), prompt the model, run the SQL, judge.

Usage:
  uv run run.py                                   # every experiment, top to bottom
  uv run run.py --only stage3                     # only experiments whose name contains "stage3"
  uv run run.py --limit 10 --results-dir results_test   # quick test on the first 10 questions

Resumable: questions already in a result file are skipped, so a restart continues where it stopped.
Each result line stores the examples the model was shown; tools/show_prompt.py rebuilds the exact prompt from it.
"""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import config
from common import bird, llm, prompt
from experiments import EXPERIMENTS, experiment_name
from stages import STAGES


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", default="", help="run only experiments whose name contains this text")
    parser.add_argument("--limit", type=int, default=None, help="only the first N questions (for testing)")
    parser.add_argument("--results-dir", type=Path, default=config.RESULTS_DIR)
    parser.add_argument("--no-report", action="store_true", help="do not rebuild the report after each experiment")
    return parser.parse_args()


# --- One question ---------------------------------------------------------------------------------------------
def evaluate_question(exp, question, stage, state):
    """Examples from the stage, then the same steps for every stage. Returns the result line."""
    examples = stage.examples_for(question, exp, state)
    generation = llm.generate(exp["model"], prompt.build_prompt(question, exp["use_evidence"], examples))
    predicted_sql = prompt.extract_sql(generation["raw_answer"])
    started = time.time()
    if predicted_sql:
        error_type, error_message = bird.judge(question["db_id"], predicted_sql, question["SQL"])
    else:
        error_type, error_message = "no_sql", "no SQL found in the answer"
    return {
        "question_id": question["question_id"],
        "db_id": question["db_id"],
        "difficulty": question["difficulty"],
        "question": question["question"],
        "evidence": question["evidence"],
        "gold_sql": question["SQL"],
        "retrieved_examples": examples,  # exactly what went into the prompt
        "raw_answer": generation["raw_answer"],
        "predicted_sql": predicted_sql,
        "correct": error_type == "correct",
        "error_type": error_type,
        "error_message": error_message,
        "seconds": generation["seconds"],  # model latency per question (generation only)
        "execution_seconds": round(time.time() - started, 3),
        "prompt_tokens": generation["prompt_tokens"],
        "output_tokens": generation["output_tokens"],
        "done_reason": generation["done_reason"],
    }


# --- Result files ---------------------------------------------------------------------------------------------
def read_done_rows(path):
    """Rows already written; a half-written last line from a crash is dropped so it gets redone."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    if text and not text.endswith("\n"):
        text = text[: text.rfind("\n") + 1]
        path.write_text(text, encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def write_meta(path, exp, progress, extra):
    """Sidecar file with everything the report needs to describe a run (model digest, hardware, status).

    progress = (number of questions, number done, finished True/False).
    """
    n_total, n_done, finished = progress
    meta = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    meta.update(extra)
    meta.update({
        "experiment": exp,
        "name": experiment_name(exp),
        "machine": config.MACHINE,
        "hardware": config.HARDWARE.get(config.MACHINE, "unknown"),
        "weights": config.MODELS[exp["model"]]["weights"],
        "generation_options": config.GENERATION_OPTIONS,
        "embedding_model": config.EMBEDDING_MODEL if exp["stage"] > 1 else None,
        "n_total": n_total,
        "n_done": n_done,
        "finished": finished,
        "updated": datetime.now().isoformat(timespec="seconds"),
    })
    meta.setdefault("started", meta["updated"])
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def progress_line(exp, n_done, n_total, n_correct):
    hint = "on" if exp["use_evidence"] else "off"
    label = f"stage{exp['stage']}"
    if exp["stage"] == 3:
        label += f" contam={exp['contamination']:.0%} seed={exp['contamination_seed']}"
    if exp["stage"] == 4:
        label += f" attack={exp['attack']}"
    accuracy = 100 * n_correct / n_done if n_done else 0.0
    return f"[{exp['model']} | {exp['dataset']} | hint={hint} | {label}] {n_done}/{n_total} EX so far {accuracy:.1f}%"


# --- One experiment -------------------------------------------------------------------------------------------
def run_experiment(exp, limit, results_dir):
    """Run one experiment to the end, skipping questions already done. Returns False if there was nothing to do."""
    name = experiment_name(exp)
    results_path = results_dir / f"{name}.jsonl"
    meta_path = results_dir / f"{name}.meta.json"
    questions = bird.load_questions(exp["dataset"])[: limit or exp.get("limit")]
    done_rows = read_done_rows(results_path)
    done_ids = {row["question_id"] for row in done_rows}
    n_correct = sum(row["correct"] for row in done_rows)
    if len(done_ids) >= len(questions):
        print(f"{name}: already complete ({len(done_ids)} questions)")
        return False
    stage = STAGES[exp["stage"]]
    state = stage.prepare(exp)  # embeds first, then the LLM loads
    llm.load_model(exp["model"])
    write_meta(meta_path, exp, (len(questions), len(done_ids), False), {
        "ollama_version": llm.ollama_version(),
        "model_details": llm.model_details(exp["model"]),
    })
    with results_path.open("a", encoding="utf-8") as results_file:
        for question in questions:
            if question["question_id"] in done_ids:
                continue
            row = evaluate_question(exp, question, stage, state)
            results_file.write(json.dumps(row, ensure_ascii=False) + "\n")
            results_file.flush()  # every finished question is on disk before the next one starts
            done_ids.add(question["question_id"])
            n_correct += row["correct"]
            if len(done_ids) == 1 or len(done_ids) % 25 == 0:
                # check that the model really sits fully in VRAM; the report flags anything below 1.0
                write_meta(meta_path, exp, (len(questions), len(done_ids), False), {"gpu": llm.gpu_share(exp["model"])})
            print(progress_line(exp, len(done_ids), len(questions), n_correct), flush=True)
    write_meta(meta_path, exp, (len(questions), len(done_ids), True), {})
    return True


def rebuild_report(results_dir):
    """Rebuild the report; a report bug must never stop the experiments, so errors are only printed."""
    from tools import report  # noqa: PLC0415 - matplotlib only loads when a report is built

    is_real = results_dir.resolve() == config.RESULTS_DIR.resolve()  # test runs must not overwrite the real report
    report_dir = config.REPORT_DIR if is_real else config.CODE_DIR / "report_test"
    try:
        report.build_report(results_dir, report_dir)
    except Exception as error:  # noqa: BLE001
        print(f"report failed: {error!r}")


def main():
    args = parse_args()
    args.results_dir.mkdir(parents=True, exist_ok=True)
    for exp in EXPERIMENTS:
        if args.only not in experiment_name(exp):
            continue
        ran = run_experiment(exp, args.limit, args.results_dir)
        if ran and not args.no_report:
            rebuild_report(args.results_dir)


if __name__ == "__main__":
    main()
