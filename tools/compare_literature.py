"""Rescore Beskorovainyi 2026's released answers with our scorer and pair them with our Stage 1 runs.

Their repo (https://github.com/beskvladimir-create/nl2sql-onprem-bench) ships one JSONL per model and stage with
the predicted SQL. Scoring their answers with bird.judge on our copy of BIRD Dev separates two causes of a gap:
the scorer (same answers, different verdict) and the setup (different answers).

Usage: uv run python -m tools.compare_literature PATH_TO_THEIR_REPO
Writes results/measurements/beskorovainyi_rescored.json, which report.py shows next to Table 1.
"""

import argparse
import json
from pathlib import Path

import config
import experiments
from common import bird

THEIR_FILES = {"qwen2.5-coder:7b": "qwen25-coder-7b.base.jsonl", "qwen2.5-coder:14b": "qwen25-coder-14b.base.jsonl"}


def our_rows(model, use_evidence):
    """{question_id: row} of our Stage 1 run on full BIRD Dev, or None if it is not finished."""
    exp = experiments.experiment(model, use_evidence, dataset="bird_dev")
    path = config.RESULTS_DIR / f"{experiments.experiment_name(exp)}.jsonl"
    if not path.exists():
        return None
    rows = {r["question_id"]: r for r in map(json.loads, path.read_text(encoding="utf-8").splitlines())}
    return rows if len(rows) == len(bird.load_dev_questions()) else None


def same_multiset(db_id, predicted_sql, gold_sql):
    """Their scorer up to version 0.1.x: sorted multisets of rows, so duplicate rows count."""
    predicted, gold = bird.execute_sql(db_id, predicted_sql), bird.execute_sql(db_id, gold_sql)
    return sorted(map(repr, predicted)) == sorted(map(repr, gold))


def rescore(their_rows, dev):
    """Our verdict for each of their answers, and how many verdicts flip only because of duplicate rows."""
    verdicts, duplicate_only = {}, 0
    for row in their_rows:
        question = dev[str(row["qid"])]
        error_type = bird.judge(row["db_id"], row["pred_sql"], question["SQL"])[0] if row["pred_sql"] else "no_sql"
        verdicts[question["question_id"]] = error_type == "correct"
        if error_type == "correct" and not row["correct"] and not same_multiset(row["db_id"], row["pred_sql"],
                                                                                  question["SQL"]):
            duplicate_only += 1
    return verdicts, duplicate_only


def paired(ours, theirs):
    """Per-question agreement between our run and their rescored answers."""
    return {
        "ex_ours": round(100 * sum(r["correct"] for r in ours.values()) / len(ours), 2),
        "both_correct": sum(ours[q]["correct"] and theirs[q] for q in ours),
        "only_ours": sum(ours[q]["correct"] and not theirs[q] for q in ours),
        "only_theirs": sum(theirs[q] and not ours[q]["correct"] for q in ours),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", type=Path, help="clone of nl2sql-onprem-bench")
    args = parser.parse_args()
    dev = {q["question_id"]: q for q in bird.load_dev_questions()}
    out = {}
    for model, file_name in THEIR_FILES.items():
        their_rows = [json.loads(line) for line in (args.repo / "results" / file_name).read_text().splitlines()]
        verdicts, duplicate_only = rescore(their_rows, dev)
        out[model] = {
            "n": len(their_rows),
            "ex_published": round(100 * sum(r["correct"] for r in their_rows) / len(their_rows), 2),
            "ex_rescored": round(100 * sum(verdicts.values()) / len(verdicts), 2),
            "gold_sql_identical": sum(r["gold_sql"].strip() == dev[str(r["qid"])]["SQL"].strip() for r in their_rows),
            "wrong_for_them_only_because_of_duplicate_rows": duplicate_only,
        }
        for use_evidence in (False, True):
            ours = our_rows(model, use_evidence)
            if ours:
                out[model][f"ours_hint_{'on' if use_evidence else 'off'}"] = paired(ours, verdicts)
        print(model, json.dumps(out[model]), flush=True)
    path = config.RESULTS_DIR / "measurements" / "beskorovainyi_rescored.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
