"""Stage 2: retrieval memory (Cook et al. 2025, NP-0). The model sees the 3 most similar solved examples.

Memory pool = the 1,036 BIRD Dev questions that are not in the Arcwise test set, with their gold SQL.
              Only entries whose gold SQL runs are kept (Cook stores only working SQL).
Retrieval   = the 3 pool questions of the same database whose question embedding (bge-m3) is most similar
              (cosine). Ties are broken by question_id, so retrieval is fully deterministic.

Stage 3 and 4 reuse this memory: Stage 3 builds it with some entries wrong (sql_overrides), Stage 4 changes what
is retrieved. Stage 2 itself knows nothing about contamination.
"""

import json
import math
import sqlite3

import config
from common import bird, llm

EMBEDDING_BATCH_SIZE = 64
POOL_CHECKS_FILE = config.CACHE_DIR / "pool_checks.json"


# --- Stage interface ------------------------------------------------------------------------------------------
def prepare(exp):
    return build_memory(exp)


def examples_for(question, exp, memory):  # noqa: ARG001 - same signature as the other stages
    return retrieve(memory, question)


# --- Pool -----------------------------------------------------------------------------------------------------
def load_memory_pool():
    """BIRD Dev questions that are not test questions; the memory is filled from these."""
    test_ids = {q["question_id"] for q in bird.load_arcwise_questions()}
    return [q for q in bird.load_dev_questions() if q["question_id"] not in test_ids]


def check_gold(entry):
    """Does the gold SQL of a pool entry run? Returns its rows too, so Stage 3 can compare wrong versions."""
    try:
        return {"gold_ok": True}, bird.execute_sql(entry["db_id"], entry["SQL"])
    except (sqlite3.Error, bird.QueryTimeoutError, bird.ResultTooLargeError) as error:
        return {"gold_ok": False, "gold_error": str(error), "wrong_sql": {}}, None


def load_pool_checks(pool):
    """{question_id: {"gold_ok": ...}} for every pool entry: does its gold SQL run? Cached on disk.

    The cache file is shared with Stage 3, which adds a "wrong_sql" field to the same entries; Stage 2 ignores it.
    """
    checks = json.loads(POOL_CHECKS_FILE.read_text(encoding="utf-8")) if POOL_CHECKS_FILE.exists() else {}
    missing = [entry for entry in pool if entry["question_id"] not in checks]
    for number, entry in enumerate(missing, start=1):
        checks[entry["question_id"]] = check_gold(entry)[0]
        if number % 100 == 0 or number == len(missing):
            print(f"checked pool SQL {number}/{len(missing)}", flush=True)
            save_pool_checks(checks)
    return checks


def save_pool_checks(checks):
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    POOL_CHECKS_FILE.write_text(json.dumps(checks, indent=1), encoding="utf-8")


# --- Embeddings -----------------------------------------------------------------------------------------------
def load_embeddings(texts):
    """Embedding vector per text, cached on disk so every run uses exactly the same vectors."""
    path = config.CACHE_DIR / f"embeddings-{config.EMBEDDING_MODEL}.json"
    vectors = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    missing = sorted({text for text in texts if text not in vectors})
    for start in range(0, len(missing), EMBEDDING_BATCH_SIZE):
        batch = missing[start : start + EMBEDDING_BATCH_SIZE]
        vectors.update(zip(batch, llm.embed(batch), strict=True))
    if missing:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(vectors), encoding="utf-8")
    return vectors


def cosine_similarity(a, b):
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


# --- Memory ---------------------------------------------------------------------------------------------------
def build_memory(exp, sql_overrides=None):
    """The memory for one experiment: every pool entry whose gold SQL runs, plus its embedding.

    sql_overrides = {question_id: (sql, label)} replaces the stored SQL of those entries and flags them
    contaminated (Stage 3 uses this). Without it the memory is clean.
    """
    sql_overrides = sql_overrides or {}
    pool = load_memory_pool()
    checks = load_pool_checks(pool)
    pool = [entry for entry in pool if checks[entry["question_id"]]["gold_ok"]]
    test_questions = bird.load_questions(exp["dataset"])
    vectors = load_embeddings([e["question"] for e in pool] + [q["question"] for q in test_questions])
    entries = []
    for entry in pool:
        sql, label = sql_overrides.get(entry["question_id"], (entry["SQL"], None))
        entries.append({
            "question_id": entry["question_id"],
            "db_id": entry["db_id"],
            "question": entry["question"],
            "sql": sql,
            "contaminated": label is not None,
            "mutation": label,
            "vector": vectors[entry["question"]],
        })
    print(f"memory: {len(entries)} examples, {len(sql_overrides)} contaminated", flush=True)
    return {"entries": entries, "question_vectors": vectors}


def retrieve(memory, question, same_database=True):
    """Top-3 most similar memory examples from the same database (cosine similarity of the questions).

    same_database=False takes them from all *other* databases instead (Stage 4 attack wrong-db).
    """
    query_vector = memory["question_vectors"][question["question"]]
    candidates = [e for e in memory["entries"] if (e["db_id"] == question["db_id"]) == same_database]
    scored = [(cosine_similarity(query_vector, e["vector"]), e["question_id"], e) for e in candidates]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [
        {
            "question_id": entry["question_id"],
            "db_id": entry["db_id"],
            "question": entry["question"],
            "sql": entry["sql"],
            "similarity": round(similarity, 4),
            "contaminated": entry["contaminated"],
            "mutation": entry["mutation"],
        }
        for similarity, _, entry in scored[: config.TOP_K_EXAMPLES]
    ]
