"""Check every finished Stage 2 to 4 run: were the right examples shown, and were the wrong ones really wrong?

Two checks per result file, no model needed (embeddings come from the cache, which the runs filled):
  1. reproduce  the current code picks the examples again for every question; they must equal the stored ones
  2. truth      every example flagged contaminated must differ from its correct SQL, every clean one must equal it
                (wrong-db examples carry correct SQL of another database, so they only need to be from another db)
It also counts how many of the shown examples were wrong, which is the share that actually reached the model.

Usage:
  uv run python -m tools.verify_examples                  # all Stage 2-4 runs in results/
  uv run python -m tools.verify_examples --only stage3    # only file names containing "stage3"
  uv run python -m tools.verify_examples --truth-only     # check 2 only, works without Ollama
Exit code 1 if any check fails.
"""

import argparse
import json
import sys

import config
from common import bird
from stages import STAGES


def load_run(meta_path):
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    rows_path = meta_path.with_name(meta_path.name.replace(".meta.json", ".jsonl"))
    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return meta["experiment"], rows


def truth_problems(row, example, gold):
    """Why an example's flag does not fit its SQL, or None if it fits."""
    if example.get("mutation") == "wrong_db":
        return None if example["db_id"] != row["db_id"] else "wrong-db example from the same database"
    correct = row["gold_sql"] if example["question_id"].startswith("poison-") else gold[example["question_id"]]
    is_same = example["sql"].strip() == correct.strip()
    if example.get("contaminated") and is_same:
        return "flagged wrong, but equals the correct SQL"
    if not example.get("contaminated") and not is_same:
        return "flagged clean, but differs from the correct SQL"
    return None


def same_examples(again, row):
    """Recomputed examples equal the stored ones, compared on the fields the stored row has.

    Rows written before 03.11.2026 have no "db_id" per example (it was added for the wrong-db attack); every
    field that is stored, including the SQL shown, must match exactly.
    """
    stored = row["retrieved_examples"]
    return len(again) == len(stored) and all(
        {key: new.get(key) for key in old} == old for new, old in zip(again, stored, strict=True)
    )


def check_run(exp, rows, gold, reproduce=True):
    """(number of reproduce mismatches, number of truth problems, examples shown, wrong examples shown, first issue)."""
    stage = STAGES[exp["stage"]]
    state = stage.prepare(exp) if reproduce else None
    questions = {q["question_id"]: q for q in bird.load_questions(exp["dataset"])}
    mismatches = problems = shown = wrong = 0
    first_issue = ""
    for row in rows:
        if reproduce and not same_examples(stage.examples_for(questions[row["question_id"]], exp, state), row):
            mismatches += 1
            first_issue = first_issue or f"question {row['question_id']}: examples differ from the stored ones"
        for example in row["retrieved_examples"]:
            shown += 1
            wrong += bool(example.get("contaminated"))
            problem = truth_problems(row, example, gold)
            if problem:
                problems += 1
                where = f"question {row['question_id']}, example {example['question_id']}"
                first_issue = first_issue or f"{where}: {problem}"
    return mismatches, problems, shown, wrong, first_issue


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", default="")
    parser.add_argument("--truth-only", action="store_true", help="skip check 1 (no Ollama or embedding cache needed)")
    args = parser.parse_args()
    gold = {q["question_id"]: q["SQL"] for q in bird.load_dev_questions()}
    failed = False
    for meta_path in sorted(config.RESULTS_DIR.glob("*.meta.json")):
        exp, rows = load_run(meta_path)
        if exp["stage"] < 2 or args.only not in meta_path.name:
            continue
        mismatches, problems, shown, wrong, issue = check_run(exp, rows, gold, reproduce=not args.truth_only)
        ok = mismatches == 0 and problems == 0
        failed |= not ok
        share = 100 * wrong / shown if shown else 0
        print(f"{'OK  ' if ok else 'FAIL'} {meta_path.name.replace('.meta.json', '')}: {len(rows)} questions, "
              f"{wrong}/{shown} shown examples wrong ({share:.1f} %), "
              f"{'reproduce skipped' if args.truth_only else f'{mismatches} reproduce mismatches'}, "
              f"{problems} flag problems {issue}", flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
