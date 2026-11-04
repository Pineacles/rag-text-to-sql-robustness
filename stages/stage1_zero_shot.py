"""Stage 1: zero-shot baseline. The model sees only the question, the schema and (hint on) the BIRD evidence.

Every stage file has the same two functions, so run.py can treat all stages alike:
  prepare(exp)                         once per experiment, builds whatever the stage needs
  examples_for(question, exp, state)   the examples shown to the model for one question
"""


def prepare(exp):  # noqa: ARG001 - same signature as the other stages
    return None


def examples_for(question, exp, state):  # noqa: ARG001
    return []
