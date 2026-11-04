"""BIRD Dev smoke test: zero-shot text-to-SQL via Ollama, execution accuracy on the first N questions.

First test of 14.10.2026, kept because Stage 1 is checked against it.
Data: https://bird-bench.oss-cn-beijing.aliyuncs.com/dev.zip, unzipped (incl. dev_databases.zip) into data/bird/.
"""

import argparse
import json
import re
import sqlite3
import time
import urllib.request
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_DATA_DIR = Path(__file__).parent.parent / "data" / "bird" / "dev_20240627"
QUERY_TIMEOUT_SECONDS = 30
GENERATION_OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 8192}
SQL_BLOCK = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
# Cook et al. 2025, Appendix B.1 "Generate SQL Prompt" (NP-0); the examples section is empty without memory.
COOK_NP0_TEMPLATE = """You are a SQL query generation agent.
Instructions for your task are as follows:
Generate a single SQL query which answers the following question that can be executed in a SQLITE3 database.
---------------------
## Question:
{question}{evidence}
## Database schema and information:
{database_schema}
## Task Output:
SQL Query:"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=20, help="number of questions from the start of dev.json")
    parser.add_argument("--model", default="qwen2.5-coder:7b")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--evidence", action="store_true", help="add the BIRD evidence hint (absent in Cook's NP-0)")
    return parser.parse_args()


def database_path(data_dir: Path, db_id: str) -> Path:
    return data_dir / "dev_databases" / db_id / f"{db_id}.sqlite"


def read_schema(db_path: Path) -> str:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute("SELECT sql FROM sqlite_master WHERE type = 'table'")
        return "\n".join(sql for (sql,) in rows if sql)


def build_prompt(schema: str, question: dict, with_evidence: bool) -> str:
    evidence = f"\n## Evidence:\n{question['evidence']}" if with_evidence else ""
    return COOK_NP0_TEMPLATE.format(question=question["question"], evidence=evidence, database_schema=schema)


def extract_sql(answer: str) -> str:
    """Models often wrap the query in prose; take the last fenced block, else the whole answer."""
    blocks = SQL_BLOCK.findall(answer)
    return (blocks[-1] if blocks else answer).strip()


def generate_sql(model: str, prompt: str) -> str:
    body = {"model": model, "prompt": prompt, "stream": False, "options": GENERATION_OPTIONS}
    if model.startswith("baa-qwen3.8"):
        body["think"] = False  # Ollama ignores the template's thinking switch for this model family
    request = urllib.request.Request(OLLAMA_URL, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(request) as response:
        answer = json.load(response)["response"]
    return extract_sql(answer)


def execute(db_path: Path, sql: str) -> set:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    started = time.time()
    connection.set_progress_handler(lambda: time.time() - started > QUERY_TIMEOUT_SECONDS, 10_000)
    try:
        return set(connection.execute(sql).fetchall())
    finally:
        connection.close()


def judge(db_path: Path, predicted_sql: str, gold_sql: str) -> str:
    """BIRD execution accuracy: OK when predicted and gold queries return the same set of rows."""
    try:
        return "OK" if execute(db_path, predicted_sql) == execute(db_path, gold_sql) else "WRONG ROWS"
    except sqlite3.Error as error:
        return f"SQL ERROR: {error}"


def main() -> None:
    args = parse_args()
    questions = json.loads((args.data / "dev.json").read_text(encoding="utf-8"))[: args.n]
    correct, durations = 0, []

    for question in questions:
        db_path = database_path(args.data, question["db_id"])
        started = time.time()
        predicted_sql = generate_sql(args.model, build_prompt(read_schema(db_path), question, args.evidence))
        durations.append(time.time() - started)
        verdict = judge(db_path, predicted_sql, question["SQL"])
        correct += verdict == "OK"
        label = f"{question['question_id']:>4} {question['db_id']:<28} {question['difficulty']:<11}"
        print(f"{label} {durations[-1]:5.1f}s  {verdict}")
        if verdict != "OK":
            print(f"      predicted: {' '.join(predicted_sql.split())}\n      gold:      {question['SQL']}")

    accuracy = 100 * correct / len(questions)
    average = sum(durations) / len(durations)
    hint = "with evidence" if args.evidence else "no evidence"
    print(
        f"\n{args.model} ({hint}): {correct}/{len(questions)} correct ({accuracy:.1f}%), {average:.1f} s/question avg"
    )


if __name__ == "__main__":
    main()
