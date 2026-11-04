"""Compare two result files question by question: same prompt, and how often the same answer and verdict?

Two levels, because they mean different things:
  prompt   the examples shown and the prompt length must be identical. A difference here is a code difference.
  answer   answer text and verdict. Even with temperature 0 on the same machine a few answers can differ, because
           the GPU does not compute bit-for-bit the same every time (it depends, for example, on what is in
           Ollama's prompt cache). This is reported, not treated as a failure.
Only questions in both files count. Examples are compared on the fields the first file stores, since rows written
before 03.11.2026 have no "db_id" per example.

Usage:
  uv run python -m tools.compare_runs results/RUN.jsonl results_test/RUN.jsonl
Exit code 1 if a prompt differs.
"""

import argparse
import json
import sys
from pathlib import Path


def load(path):
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {row["question_id"]: row for row in map(json.loads, lines)}


def same_examples(old, new):
    old_examples, new_examples = old["retrieved_examples"], new["retrieved_examples"]
    return len(old_examples) == len(new_examples) and all(
        {key: b.get(key) for key in a} == a for a, b in zip(old_examples, new_examples, strict=True)
    )


def report(label, questions, total):
    detail = "same" if not questions else f"{len(questions)} of {total} differ, e.g. question {questions[0]}"
    print(f"  {label:28s} {detail}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    args = parser.parse_args()
    first, second = load(args.first), load(args.second)
    common = [qid for qid in first if qid in second]
    print(f"{len(common)} questions in both files")

    print("prompt (must be the same):")
    examples = [q for q in common if not same_examples(first[q], second[q])]
    tokens = [q for q in common if first[q]["prompt_tokens"] != second[q]["prompt_tokens"]]
    report("examples shown", examples, len(common))
    report("prompt tokens", tokens, len(common))

    print("answer (small differences possible, see above):")
    report("answer text", [q for q in common if first[q]["raw_answer"] != second[q]["raw_answer"]], len(common))
    verdicts = [q for q in common if first[q]["correct"] != second[q]["correct"]]
    report("verdict (correct or not)", verdicts, len(common))
    n_first = sum(first[q]["correct"] for q in common)
    n_second = sum(second[q]["correct"] for q in common)
    print(f"  correct: {n_first} vs {n_second} of {len(common)}")
    sys.exit(1 if examples or tokens or not common else 0)


if __name__ == "__main__":
    main()
