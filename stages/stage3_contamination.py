"""Stage 3: random contamination. Like Stage 2, but a share of the memory pool (5 / 10 / 20 %) holds wrong SQL.

A wrong version is a near-miss of the gold SQL, made by one of the rules below (Gao et al. 2024 call this
"relevant noise"). It only counts if it still runs, returns rows, and returns *different* rows than the gold SQL,
so every contaminated example is executable but semantically wrong.

Which entries are wrong is chosen once per (level, seed), before any question is asked. Retrieval is unchanged,
so a wrong example reaches the model only when it happens to be among the 3 most similar.
"""

import random
import re
import sqlite3

from common import bird
from stages import stage2_retrieval

# Each rule changes the FIRST match of a pattern into a plausible mistake an annotator or a model could make.
MUTATION_RULES = [
    ("desc_to_asc", r"\bDESC\b", "ASC"),
    ("asc_to_desc", r"\bASC\b", "DESC"),
    ("max_to_min", r"\bMAX\s*\(", "MIN("),
    ("min_to_max", r"\bMIN\s*\(", "MAX("),
    ("sum_to_avg", r"\bSUM\s*\(", "AVG("),
    ("avg_to_sum", r"\bAVG\s*\(", "SUM("),
    ("flip_greater", r"(?<![<>!])>=?", lambda match: match.group(0).replace(">", "<")),
    ("flip_less", r"<(?!>)=?", lambda match: match.group(0).replace("<", ">")),
    ("limit_plus_one", r"\bLIMIT\s+(\d+)", lambda match: f"LIMIT {int(match.group(1)) + 1}"),
    ("equals_to_not_equals", r"(?<![<>!=])=\s*'", "<> '"),
    ("and_to_or", r"\bAND\b", "OR"),
]


# --- Stage interface ------------------------------------------------------------------------------------------
def prepare(exp):
    """Choose the wrong entries for this level and seed, then build the Stage 2 memory with them swapped in."""
    pool = stage2_retrieval.load_memory_pool()
    checks = load_wrong_sql(pool)
    pool_size = sum(checks[entry["question_id"]]["gold_ok"] for entry in pool)
    rule_by_id = choose_contamination(checks, exp["contamination"], exp["contamination_seed"], pool_size)
    overrides = {qid: (checks[qid]["wrong_sql"][rule], rule) for qid, rule in rule_by_id.items()}
    return stage2_retrieval.build_memory(exp, overrides)


def examples_for(question, exp, memory):  # noqa: ARG001 - same signature as the other stages
    return stage2_retrieval.retrieve(memory, question)


# --- Making SQL wrong -----------------------------------------------------------------------------------------
def mutate(sql):
    """All rule-based near-miss versions of a query, as {rule name: changed SQL}."""
    candidates = {}
    for name, pattern, replacement in MUTATION_RULES:
        if name == "and_to_or" and re.search(r"\bBETWEEN\b", sql, re.IGNORECASE):
            continue  # BETWEEN x AND y must keep its AND
        changed = re.sub(pattern, replacement, sql, count=1, flags=re.IGNORECASE)
        if changed != sql:
            candidates[name] = changed
    return candidates


def find_wrong_sql(entry, gold_rows):
    """The mutations of a pool entry that run, return rows, and return different rows than the gold SQL."""
    wrong_sql = {}
    for name, changed in mutate(entry["SQL"]).items():
        try:
            rows = bird.execute_sql(entry["db_id"], changed)
        except (sqlite3.Error, sqlite3.Warning, bird.QueryTimeoutError, bird.ResultTooLargeError):
            continue
        if rows and rows != gold_rows:
            wrong_sql[name] = changed
    return wrong_sql


def load_wrong_sql(pool):
    """Stage 2's pool checks plus, for every working entry, its valid wrong versions ("wrong_sql"). Cached.

    Stored in the same cache file as the Stage 2 checks, so existing caches (and thus results) stay valid.
    """
    checks = stage2_retrieval.load_pool_checks(pool)
    missing = [e for e in pool if checks[e["question_id"]]["gold_ok"] and "wrong_sql" not in checks[e["question_id"]]]
    for number, entry in enumerate(missing, start=1):
        _, gold_rows = stage2_retrieval.check_gold(entry)
        checks[entry["question_id"]]["wrong_sql"] = find_wrong_sql(entry, gold_rows)
        if number % 100 == 0 or number == len(missing):
            print(f"made wrong SQL {number}/{len(missing)}", flush=True)
            stage2_retrieval.save_pool_checks(checks)
    return checks


def choose_contamination(checks, rate, seed, pool_size):
    """Which pool entries get wrong SQL, and with which rule: {question_id: rule name}.

    The seed shuffles the entries that have a valid wrong version and picks a rule for each; then the first
    rate x pool_size entries are taken. So for one seed the 5 % set is part of the 10 % set, which is part of the
    20 % set, and an entry always gets the same wrong SQL: the levels differ only in how much is wrong.
    """
    rng = random.Random(seed)
    corruptible = sorted(qid for qid, check in checks.items() if check.get("wrong_sql"))
    rng.shuffle(corruptible)
    rules = {qid: rng.choice(sorted(checks[qid]["wrong_sql"])) for qid in corruptible}
    count = round(rate * pool_size)
    return {qid: rules[qid] for qid in corruptible[:count]}
