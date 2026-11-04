"""Show exactly what the model saw for one question of a finished run, and which examples were wrong.

The prompt is rebuilt from the result line with the same build_prompt the run used; the examples in a result line
are the ones that went into the prompt (run.py stores them as they were passed on). For every example the script
also shows the correct SQL next to the one shown, so a contaminated example can be checked by eye.

Usage:
  uv run python -m tools.show_prompt RESULT_FILE [QUESTION_ID]
  (without QUESTION_ID: the first question that was shown a wrong example)
  uv run python -m tools.show_prompt results/...stage3__contam20__seed1.jsonl 1471
"""

import argparse
import difflib
import json
from pathlib import Path

from common import bird, prompt


def correct_sql_by_id():
    """Gold SQL per BIRD Dev question_id: the clean version of every memory example."""
    return {q["question_id"]: q["SQL"] for q in bird.load_dev_questions()}


def load_row(path, question_id):
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if question_id is None:
        wrong = [r for r in rows if any(e.get("contaminated") for e in r["retrieved_examples"])]
        if not wrong:
            raise SystemExit("no question in this run was shown a wrong example")
        return wrong[0]
    for row in rows:
        if row["question_id"] == question_id:
            return row
    raise SystemExit(f"question {question_id} is not in {path.name}")


def use_evidence(path):
    meta = json.loads(path.with_name(path.name.replace(".jsonl", ".meta.json")).read_text(encoding="utf-8"))
    return meta["experiment"]["use_evidence"]


def clean_version(example, row, gold):
    """The correct SQL for an example: its own gold SQL, or the test question's gold SQL for a planted one."""
    if example["question_id"].startswith("poison-"):
        return row["gold_sql"]
    return gold.get(example["question_id"], "")


def describe_example(number, example, row, gold):
    clean = clean_version(example, row, gold)
    status = "WRONG" if example.get("contaminated") else "clean"
    lines = [f"Example {number}: {status}  (mutation: {example.get('mutation')}, similarity {example['similarity']})"]
    lines.append(f"  question: {example['question']}")
    if example["sql"].strip() == clean.strip():
        lines.append("  SQL shown = correct SQL")
    else:
        lines.append(f"  correct: {clean}")
        lines.append(f"  shown:   {example['sql']}")
        changed = [d[2:] for d in difflib.ndiff(clean.split(), example["sql"].split()) if d[0] in "+-"]
        lines.append(f"  changed words: {' | '.join(changed)}")
    if example.get("mutation") == "wrong_db":
        lines.append(f"  (correct SQL, but for database {example.get('db_id')}, not {row['db_id']})")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("result_file", type=Path)
    parser.add_argument("question_id", nargs="?", default=None)
    parser.add_argument("--no-schema", action="store_true", help="leave the (long) schema out of the printout")
    args = parser.parse_args()

    row = load_row(args.result_file, args.question_id)
    question = {"question": row["question"], "evidence": row["evidence"], "db_id": row["db_id"]}
    text = prompt.build_prompt(question, use_evidence(args.result_file), row["retrieved_examples"])
    if args.no_schema:
        start = text.index("## Database schema and information:")
        end = text.index("## Task Output:")
        text = text[:start] + "## Database schema and information:\n[schema left out]\n" + text[end:]

    print("=" * 100, "\nPROMPT SENT TO THE MODEL\n" + "=" * 100)
    print(text)
    print("=" * 100, "\nEXAMPLES CHECKED AGAINST THE CORRECT SQL\n" + "=" * 100)
    gold = correct_sql_by_id()
    for number, example in enumerate(row["retrieved_examples"], start=1):
        print(describe_example(number, example, row, gold), "\n")
    print("=" * 100, "\nMODEL ANSWER\n" + "=" * 100)
    print(f"predicted: {row['predicted_sql']}\ngold:      {row['gold_sql']}\nverdict:   {row['error_type']}")
    print(f"prompt tokens counted by Ollama: {row['prompt_tokens']}")


if __name__ == "__main__":
    main()
