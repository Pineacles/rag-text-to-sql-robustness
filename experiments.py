"""Every experiment of the thesis, one entry per result file.

An experiment is a plain dict: model, hint on/off (use_evidence), dataset, stage, plus the stage's own settings
(contamination level and seed for Stage 3, attack name and question limit for Stage 4).
run.py works through EXPERIMENTS top to bottom and skips what is already finished, so adding a run means adding a
line here.
"""

from config import MACHINE, MAIN_MODEL, MID_MODEL, SMALL_MODEL

DATASETS = ("arcwise_plat", "bird_dev")  # 498 verified questions / 1,534 original BIRD Dev questions


# --- How an experiment is described ---------------------------------------------------------------------------
def experiment(model, use_evidence, dataset="arcwise_plat", stage=1):
    """Stage 1 or 2. Stage 1 = no examples, Stage 2 = the three most similar clean examples."""
    return {
        "model": model,
        "use_evidence": use_evidence,
        "dataset": dataset,
        "stage": stage,
        "contamination": 0.0,
        "contamination_seed": None,
    }


def contaminated_experiment(model, use_evidence, contamination, contamination_seed):
    """Stage 3: a share of the memory pool gets wrong SQL, chosen with the given seed."""
    exp = experiment(model, use_evidence, stage=3)
    exp["contamination"] = contamination
    exp["contamination_seed"] = contamination_seed
    return exp


def attack_experiment(model, use_evidence, attack, limit=100):
    """Stage 4: one worst-case attack (stages/stage4_attacks.py) on the first `limit` verified questions."""
    exp = experiment(model, use_evidence, stage=4)
    exp["attack"] = attack
    exp["limit"] = limit
    return exp


def experiment_name(exp):
    """File name of the result, e.g. server-rtx5060ti__arcwise_plat__qwen2.5-coder-7b__hint-off__stage1."""
    model = exp["model"].replace(":", "-").replace("/", "-")
    hint = "hint-on" if exp["use_evidence"] else "hint-off"
    name = f"{MACHINE}__{exp['dataset']}__{model}__{hint}__stage{exp['stage']}"
    if exp["stage"] == 3:
        name += f"__contam{round(exp['contamination'] * 100):02d}__seed{exp['contamination_seed']}"
    if exp["stage"] == 4:
        name += f"__{exp['attack']}__n{exp['limit']}"
    return name


# --- The experiments, in run order (fast 7B runs before the 27B) ------------------------------------------------
HINTS = (False, True)
CONTAMINATION_LEVELS = [0.05, 0.10, 0.20]
CONTAMINATION_SEEDS = [1, 2, 3]
ATTACKS = ["top1-rule", "top1-schema", "all3-rule", "all3-schema", "poison-rule", "poison-schema"]
HARSH_ATTACKS = ["poison-swap", "poison-all3", "poison-schema-x2", "wrong-db"]

EXPERIMENTS = []

# Stage 1: zero-shot baseline, verified subset and full BIRD Dev
for dataset in DATASETS:
    for model in (SMALL_MODEL, MAIN_MODEL):
        EXPERIMENTS += [experiment(model, hint, dataset) for hint in HINTS]

# Stage 2: retrieval memory, top-3 clean examples
for model in (SMALL_MODEL, MAIN_MODEL):
    EXPERIMENTS += [experiment(model, hint, stage=2) for hint in HINTS]

# Stage 3: random contamination of the memory pool, every level with three seeds
for model in (SMALL_MODEL, MAIN_MODEL):
    for hint in HINTS:
        for level in CONTAMINATION_LEVELS:
            EXPERIMENTS += [contaminated_experiment(model, hint, level, seed) for seed in CONTAMINATION_SEEDS]

# Stage 4: worst-case attacks on the first 100 verified questions (clean reference = Stage 2 on the same questions)
for model in (SMALL_MODEL, MAIN_MODEL):
    EXPERIMENTS += [attack_experiment(model, False, attack) for attack in ATTACKS]

# Literature comparison (Beskorovainyi 2026, Table 1): the 14B, Stage 1 on full BIRD Dev
EXPERIMENTS += [experiment(MID_MODEL, hint, dataset="bird_dev") for hint in HINTS]

# Stage 4b: harsher attacks with hint off, then all ten attacks with hint on
for model in (SMALL_MODEL, MAIN_MODEL):
    EXPERIMENTS += [attack_experiment(model, False, attack) for attack in HARSH_ATTACKS]
    EXPERIMENTS += [attack_experiment(model, True, attack) for attack in ATTACKS + HARSH_ATTACKS]
