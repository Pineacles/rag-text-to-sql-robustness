"""Stage 4: worst-case contamination on a small batch (first 100 verified questions).

Goal: find where the "no effect" result of Stage 3 breaks.

Stage 3 spread wrong SQL over the memory pool and flipped one symbol (DESC/ASC, =/<>). The models were not hurt,
because a wrong example belongs to a different question and its flipped symbol rarely carries over. Stage 4 makes
the wrong examples as persuasive as possible, along two axes:

Where the wrong examples go (placement):
  top1    the most similar retrieved example is always replaced by a wrong version
  all3    all three retrieved examples are replaced by wrong versions
  poison  the test question itself is planted as the top example with a wrong SQL (similarity 1.0); the two most
          similar pool examples follow, clean. This is the knowledge-base poisoning case (Lin et al. 2025).

What is wrong (kind):
  rule    the Stage 3 near-miss rules (stage3_contamination.MUTATION_RULES)
  schema  one column is swapped for another column of the same table with the same type, e.g. a wrong join key,
          a wrong filter column or a wrong output column. Models take exactly this kind of knowledge (column names,
          join paths) from examples, so it is the kind most likely to transfer.

Stage 4b: Stage 4 barely hurt, and poison even helped, because a near-miss of the gold SQL still hands over the
right tables and joins and the model repairs it. Four harsher attacks remove that help:
  poison-swap       the test question is planted with the gold SQL of a *different* pool question of the same
                    database: it runs and returns rows, but answers another question, so there is nothing to repair
  poison-all3       planted wrong SQL and the other two examples wrong too (schema swap, rule if no swap works):
                    nothing clean is shown
  poison-schema-x2  like poison-schema, but two swaps at two different column references, so a fix takes two edits
  wrong-db          the three most similar examples from *other* databases: correct SQL for the wrong schema,
                    to test whether irrelevant examples confuse the model

A wrong version only counts if it runs, returns at least one row, and returns a different set of rows than the
correct query (same check as Stage 3). An example without such a version stays clean and is recorded as such.
Everything is deterministic: candidates are tried in a fixed, seeded order, and results are cached.
"""

import json
import random
import re
import sqlite3
from functools import cache

import config
from common import bird
from stages import stage2_retrieval, stage3_contamination

ATTACKS = (
    "top1-rule", "top1-schema", "all3-rule", "all3-schema", "poison-rule", "poison-schema",
    "poison-swap", "poison-all3", "poison-schema-x2", "wrong-db",
)
SEED = 4
MAX_SCHEMA_TRIES = 12  # executed candidate queries per SQL, so one long query cannot stall the setup


# --- Stage interface ------------------------------------------------------------------------------------------
def prepare(exp):
    """Clean Stage 2 memory, plus the cache of validated wrong SQL so every run sees the same wrong versions."""
    return {"memory": stage2_retrieval.build_memory(exp), "wrong_sql": WrongSqlCache()}


def examples_for(question, exp, state):
    """Retrieve exactly as in Stage 2, then let the attack change what the model sees."""
    examples = stage2_retrieval.retrieve(state["memory"], question)
    examples = apply(exp["attack"], question, examples, state["wrong_sql"], state["memory"])
    state["wrong_sql"].save()
    return examples


def apply(attack, question, examples, wrong_sql, memory_store):
    """The examples the model sees under one attack, e.g. attack = "top1-schema"."""
    db_id, keep = question["db_id"], config.TOP_K_EXAMPLES - 1
    if attack not in ATTACKS:
        raise ValueError(f"unknown attack {attack}")
    if attack == "wrong-db":
        others = stage2_retrieval.retrieve(memory_store, question, same_database=False)
        return [{**e, "contaminated": True, "mutation": "wrong_db"} for e in others]
    if attack == "poison-all3":
        # schema swaps are copied most (Stage 4); rule as fallback so as few clean examples as possible remain
        kinds = ("schema", "rule")
        wrong = first_wrong(wrong_sql, kinds, db_id, question["SQL"])
        rest = [corrupt(e, db_id, kinds, wrong_sql) for e in examples]
        return [planted(question, wrong), *rest[:keep]] if wrong else rest
    placement, kind = attack.split("-", 1)
    if placement == "top1":
        return [corrupt(examples[0], db_id, (kind,), wrong_sql), *examples[1:]]
    if placement == "all3":
        return [corrupt(e, db_id, (kind,), wrong_sql) for e in examples]
    others = swap_sources(question, examples[:keep], memory_store) if kind == "swap" else ()
    wrong = wrong_sql.get({"schema-x2": "schema2"}.get(kind, kind), db_id, question["SQL"], others)
    if wrong is None:
        return examples  # nothing to plant; recorded through contaminated=False everywhere
    return [planted(question, wrong), *examples[:keep]]


# --- Schema mutation ------------------------------------------------------------------------------------------
STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
TABLE_REF = re.compile(
    r"\b(?:FROM|JOIN)\s+(`[^`]+`|\"[^\"]+\"|\w+)(?:\s+(?:AS\s+)?(?!(?:ON|WHERE|INNER|LEFT|RIGHT|JOIN|GROUP|ORDER"
    r"|LIMIT|UNION|CROSS|NATURAL|USING|HAVING)\b)(\w+))?",
    re.IGNORECASE,
)
# a column reference: optional alias/table qualifier, then a bare, backticked or double-quoted name
COLUMN_REF = re.compile(r"(?:(\w+)\s*\.\s*)?(`[^`]+`|\"[^\"]+\"|\b[A-Za-z_]\w*\b)")
SQL_WORDS = {
    "select", "from", "where", "and", "or", "not", "in", "is", "null", "as", "on", "join", "inner", "left", "right",
    "outer", "group", "by", "order", "asc", "desc", "limit", "having", "distinct", "count", "sum", "avg", "min",
    "max", "case", "when", "then", "else", "end", "cast", "real", "integer", "text", "like", "between", "union",
    "all", "exists", "iif", "strftime", "substr", "length", "round", "abs", "date", "julianday", "offset", "with",
    "cross", "using", "natural", "except", "intersect", "instr", "replace", "lower", "upper", "trim", "coalesce",
    "ifnull", "float", "true", "false", "glob", "escape", "over", "partition", "rank", "row_number", "datetime",
}


def unquote(name):
    return name[1:-1] if name[:1] in "`\"" else name


def quote(name):
    return name if re.fullmatch(r"[A-Za-z_]\w*", name) else f"`{name}`"


@cache
def table_columns(db_id):
    """{table name lower: [(column name, declared type lower), ...]} for one database."""
    connection = sqlite3.connect(f"file:{bird.database_path(db_id)}?mode=ro", uri=True)
    try:
        tables = [name for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        return {
            table.lower(): [
                (row[1], (row[2] or "").lower()) for row in connection.execute(f'PRAGMA table_info("{table}")')
            ]
            for table in tables
        }
    finally:
        connection.close()


@cache
def value_exists(db_id, table, column, literal):
    """Does the quoted literal occur in this column? Keeps a swapped filter column plausible."""
    connection = sqlite3.connect(f"file:{bird.database_path(db_id)}?mode=ro", uri=True)
    try:
        query = f'SELECT 1 FROM "{table}" WHERE "{column}" = {literal} LIMIT 1'
        return connection.execute(query).fetchone() is not None
    except sqlite3.Error:
        return False
    finally:
        connection.close()


def section_of(sql, position):
    """Which clause a position in the query belongs to: join (ON), filter (WHERE/HAVING) or select."""
    before = sql[:position].upper()
    words = (" ON ", " WHERE ", " HAVING ", "SELECT ", " GROUP BY ", " ORDER BY ")
    marks = {word: before.rfind(word) for word in words}
    last = max(marks, key=marks.get)
    if marks[last] < 0:
        return "select"
    return {" ON ": "join", " WHERE ": "filter", " HAVING ": "filter"}.get(last, "select")


def alias_map(sql, columns):
    """{table name or alias lower: table name lower} for the tables a query reads."""
    aliases = {}
    for raw_table, alias in TABLE_REF.findall(sql):
        table = unquote(raw_table).lower()
        if table in columns:
            aliases[table] = table
            if alias:
                aliases[alias.lower()] = table
    return aliases


def owning_table(qualifier, name, aliases, columns):
    """The one table a column reference belongs to, or None if unknown or ambiguous."""
    if qualifier:
        return aliases.get(qualifier.lower())
    tables = [t for t in sorted(set(aliases.values())) if any(c.lower() == name.lower() for c, _ in columns[t])]
    return tables[0] if len(tables) == 1 else None


def column_swaps(db_id, sql, match, aliases, columns):
    """All (section, position, changed SQL) for one column reference: same table, same type, plausible filter value."""
    qualifier, raw_name = match.group(1), match.group(2)
    name = unquote(raw_name)
    if raw_name.lower() in SQL_WORDS or name.lower() in aliases:
        return []
    table = owning_table(qualifier, name, aliases, columns)
    own_type = next((typ for c, typ in columns.get(table, []) if c.lower() == name.lower()), None)
    if own_type is None:
        return []
    start, end = match.span(2)
    literal = re.match(r"\s*(?:=|<>|!=)\s*('(?:[^']|'')*')", sql[end:])
    swaps = []
    for alternative, typ in columns[table]:
        if typ != own_type or alternative.lower() == name.lower():
            continue
        if literal and not value_exists(db_id, table, alternative, literal.group(1)):
            continue  # e.g. Currency = 'SME': the value never occurs in the new column, an easy tell
        swaps.append((section_of(sql, start), start, f"{sql[:start]}{quote(alternative)}{sql[end:]}"))
    return swaps


def schema_swaps(db_id, sql):
    """Every single-column swap of a query, as (section, position, changed SQL), join keys first, then filters."""
    columns = table_columns(db_id)
    aliases = alias_map(sql, columns)
    masked = STRING_LITERAL.sub(lambda m: " " * len(m.group(0)), sql)  # never touch text inside 'literals'
    found = []
    for match in COLUMN_REF.finditer(masked):
        found.extend(column_swaps(db_id, sql, match, aliases, columns))
    order = {"join": 0, "filter": 1, "select": 2}
    random.Random(SEED).shuffle(found)  # fixed order inside each section, so no column is always tried first
    found.sort(key=lambda item: order[item[0]])
    return found


def schema_candidates(db_id, sql):
    """Single swaps as (mutation name, changed SQL)."""
    return [(f"schema_{section}", changed) for section, _, changed in schema_swaps(db_id, sql)]


def double_schema_candidates(db_id, sql):
    """Two swaps at two different column references, as (mutation name, changed SQL).

    The second swap is searched in the once-changed query; a swap at the same position would only touch the first
    reference again, so it is skipped. Two second swaps per first one keep the list varied and short.
    """
    candidates = []
    for section, position, once in schema_swaps(db_id, sql)[:MAX_SCHEMA_TRIES]:
        second = [(s, twice) for s, p, twice in schema_swaps(db_id, once) if p != position]
        candidates.extend((f"schema2_{section}_{s}", twice) for s, twice in second[:2])
    return candidates


# --- Validated wrong versions ---------------------------------------------------------------------------------
def first_valid(db_id, correct_sql, candidates, max_tries):
    """The first candidate that runs, returns rows, and returns different rows than the correct query."""
    try:
        correct_rows = bird.execute_sql(db_id, correct_sql)
    except (sqlite3.Error, bird.QueryTimeoutError, bird.ResultTooLargeError):
        return None
    for name, changed in candidates[:max_tries]:
        try:
            rows = bird.execute_sql(db_id, changed)
        except (sqlite3.Error, sqlite3.Warning, bird.QueryTimeoutError, bird.ResultTooLargeError):
            continue
        if rows and rows != correct_rows:
            return {"mutation": name, "sql": changed}
    return None


class WrongSqlCache:
    """Validated wrong SQL per (kind, database, correct SQL), kept on disk so every run sees the same versions."""

    def __init__(self):
        self.path = config.CACHE_DIR / "attack_wrong_sql.json"
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.dirty = False

    def get(self, kind, db_id, sql, others=()):
        """kind: rule, schema, schema2 (two swaps) or swap (others = SQL of other questions to take one from)."""
        key = f"{kind}|{db_id}|{sql}"
        if key not in self.data:
            if kind == "rule":
                rules = sorted(stage3_contamination.mutate(sql).items())
                random.Random(f"{SEED}|{sql}").shuffle(rules)
                self.data[key] = first_valid(db_id, sql, rules, len(rules))
            elif kind == "schema":
                self.data[key] = first_valid(db_id, sql, schema_candidates(db_id, sql), MAX_SCHEMA_TRIES)
            elif kind == "schema2":
                self.data[key] = first_valid(db_id, sql, double_schema_candidates(db_id, sql), MAX_SCHEMA_TRIES)
            elif kind == "swap":
                swaps = [(f"swap_{question_id}", other) for question_id, other in others]
                random.Random(f"{SEED}|{sql}").shuffle(swaps)
                self.data[key] = first_valid(db_id, sql, swaps, MAX_SCHEMA_TRIES)
            else:
                raise ValueError(f"unknown kind {kind}")
            self.dirty = True
        return self.data[key]

    def save(self):
        if self.dirty:
            config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
            self.dirty = False


def first_wrong(wrong_sql, kinds, db_id, sql):
    """The validated wrong version of the first kind that has one, or None."""
    return next((w for w in (wrong_sql.get(kind, db_id, sql) for kind in kinds) if w), None)


def corrupt(example, db_id, kinds, wrong_sql):
    """A retrieved example with its SQL replaced by a validated wrong version (or unchanged if there is none)."""
    wrong = first_wrong(wrong_sql, kinds, db_id, example["sql"])
    if wrong is None:
        return {**example, "contaminated": False, "mutation": None, "attack_note": "no valid wrong version"}
    return {**example, "sql": wrong["sql"], "contaminated": True, "mutation": wrong["mutation"]}


def planted(question, wrong):
    """The test question itself as the top example, with a wrong SQL (similarity 1.0)."""
    return {
        "question_id": f"poison-{question['question_id']}",
        "question": question["question"],
        "sql": wrong["sql"],
        "similarity": 1.0,
        "contaminated": True,
        "mutation": wrong["mutation"],
    }


def swap_sources(question, examples, memory_store):
    """(question_id, gold SQL) of the same-database pool questions that are not shown anyway, for poison-swap."""
    shown = {e["question_id"] for e in examples}
    return sorted((e["question_id"], e["sql"]) for e in memory_store["entries"]
                  if e["db_id"] == question["db_id"] and e["question_id"] not in shown)
