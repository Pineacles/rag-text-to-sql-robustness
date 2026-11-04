"""What the model sees and how its answer is read: the NP-0 prompt template and the SQL extraction."""

import re

from common import bird

# Cook et al. 2025, Appendix B.1 "Generate SQL Prompt" (NP-0), single call, no refine step.
# {examples_block} is empty in Stage 1, which makes the prompt identical to the 14.10. smoke test.
PROMPT_TEMPLATE = """You are a SQL query generation agent.
Instructions for your task are as follows:
Generate a single SQL query which answers the following question that can be executed in a SQLITE3 database.
{examples_block}---------------------
## Question:
{question}{evidence}
## Database schema and information:
{database_schema}
## Task Output:
SQL Query:"""

# Cook's examples section; each example is formatted like their NP-0 trajectory (Appendix E.1).
EXAMPLES_BLOCK_TEMPLATE = """---------------------
Examples
{examples}
"""
EXAMPLE_TEMPLATE = """Similar Example
Question: {question}
SQL:
{sql}"""

SQL_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def build_prompt(question, use_evidence, examples):
    """Fill the NP-0 template; the BIRD hint and the retrieved examples are optional parts."""
    evidence = f"\n## Evidence:\n{question['evidence']}" if use_evidence else ""
    examples_block = ""
    if examples:
        formatted = [EXAMPLE_TEMPLATE.format(question=e["question"], sql=e["sql"]) for e in examples]
        examples_block = EXAMPLES_BLOCK_TEMPLATE.format(examples="\n\n".join(formatted))
    return PROMPT_TEMPLATE.format(
        examples_block=examples_block,
        question=question["question"],
        evidence=evidence,
        database_schema=bird.read_schema(question["db_id"]),
    )


def extract_sql(raw_answer):
    """Take the last ```sql fenced block (models wrap SQL in prose), otherwise the whole answer.

    Returns "" when the text contains no SELECT/WITH at all; that question is counted as no_sql.
    """
    blocks = SQL_FENCE.findall(raw_answer)
    sql = (blocks[-1] if blocks else raw_answer).strip()
    if not re.search(r"\b(select|with)\b", sql, re.IGNORECASE):
        return ""
    return sql
