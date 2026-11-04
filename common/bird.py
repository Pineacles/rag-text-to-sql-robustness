"""BIRD data access: load questions, read schemas, execute SQL and judge a prediction against the gold query."""

import json
import sqlite3
import time
from functools import cache

import config


def load_dev_questions():
    """All 1,534 BIRD Dev questions, with question_id as a string (Arcwise uses strings)."""
    questions = json.loads((config.BIRD_DEV_DIR / "dev.json").read_text(encoding="utf-8"))
    for question in questions:
        question["question_id"] = str(question["question_id"])
    return questions


def load_arcwise_questions():
    """The 498 verified Arcwise-Plat questions (corrected question, evidence and SQL).

    Arcwise has no difficulty field; it is a corrected BIRD Mini-Dev, so every question_id is also a BIRD Dev
    question_id and we take the difficulty from there.
    """
    difficulty_by_id = {q["question_id"]: q["difficulty"] for q in load_dev_questions()}
    questions = json.loads(config.ARCWISE_FILE.read_text(encoding="utf-8"))
    return [
        {
            "question_id": q["question_id"],
            "db_id": q["db_id"],
            "question": q["question"],
            "evidence": q["evidence"],
            "SQL": q["SQL"],
            "difficulty": difficulty_by_id[q["question_id"]],
        }
        for q in questions
    ]


def load_questions(dataset):
    """Questions of one dataset name from config: arcwise_plat or bird_dev."""
    if dataset == "arcwise_plat":
        return load_arcwise_questions()
    if dataset == "bird_dev":
        return load_dev_questions()
    raise ValueError(f"unknown dataset {dataset}")


def database_path(db_id):
    return config.BIRD_DEV_DIR / "dev_databases" / db_id / f"{db_id}.sqlite"


@cache
def read_schema(db_id):
    """The CREATE TABLE statements of a database, which is the schema the model sees."""
    connection = sqlite3.connect(f"file:{database_path(db_id)}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT sql FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        connection.close()
    return "\n".join(sql for (sql,) in rows if sql)


class QueryTimeoutError(Exception):
    """The query ran longer than config.SQL_TIMEOUT_SECONDS."""


class ResultTooLargeError(Exception):
    """The query returned more than config.MAX_RESULT_ROWS rows."""


def execute_sql(db_id, sql):
    """Run a query read-only and return its rows as a set (BIRD compares sets, see judge)."""
    connection = sqlite3.connect(f"file:{database_path(db_id)}?mode=ro", uri=True)
    started = time.time()
    # SQLite calls this every 10,000 steps; returning True aborts the query.
    connection.set_progress_handler(lambda: time.time() - started > config.SQL_TIMEOUT_SECONDS, 10_000)
    try:
        cursor = connection.execute(sql)
        rows = []
        while batch := cursor.fetchmany(10_000):
            rows.extend(batch)
            if len(rows) > config.MAX_RESULT_ROWS:
                raise ResultTooLargeError(f"more than {config.MAX_RESULT_ROWS} rows")
        return set(rows)
    except sqlite3.OperationalError as error:
        if time.time() - started > config.SQL_TIMEOUT_SECONDS:
            raise QueryTimeoutError(f"longer than {config.SQL_TIMEOUT_SECONDS} s") from error
        raise
    finally:
        connection.close()


def judge(db_id, predicted_sql, gold_sql):
    """Return (error_type, message) for a prediction.

    Methodological choice: BIRD execution accuracy. A prediction is correct when it returns the same SET of rows
    as the gold query (row order and duplicate rows are ignored), exactly as BIRD's evaluation.py does with
    set(predicted_res) == set(ground_truth_res).
    Error types: correct, wrong_result, sql_error (query crashed), timeout. no_sql is decided before this.
    gold_error only appears if the gold query itself fails, which would be a data problem, not a model error.
    """
    try:
        gold_rows = execute_sql(db_id, gold_sql)
    except (sqlite3.Error, QueryTimeoutError, ResultTooLargeError) as error:
        return "gold_error", str(error)
    try:
        predicted_rows = execute_sql(db_id, predicted_sql)
    except QueryTimeoutError as error:
        return "timeout", str(error)
    except ResultTooLargeError as error:
        return "wrong_result", str(error)  # the gold result is smaller, so it cannot match
    except (sqlite3.Error, sqlite3.Warning) as error:
        return "sql_error", str(error)
    if predicted_rows == gold_rows:
        return "correct", ""
    return "wrong_result", ""
