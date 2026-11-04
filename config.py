"""Settings: where the data lives, which machine and models, and the fixed generation options.

Nothing in here decides *what* runs; that is experiments.py.
"""

import os
from pathlib import Path

# --- Paths ---------------------------------------------------------------------------------------------------
CODE_DIR = Path(__file__).parent
DATA_DIR = CODE_DIR / "data"  # not in git, see README "Data"
RESULTS_DIR = CODE_DIR / "results"
REPORT_DIR = CODE_DIR / "report"
CACHE_DIR = DATA_DIR / "cache"  # embeddings and validated wrong SQL, rebuilt automatically if deleted

BIRD_DEV_DIR = DATA_DIR / "bird" / "dev_20240627"  # https://bird-bench.oss-cn-beijing.aliyuncs.com/dev.zip
ARCWISE_FILE = DATA_DIR / "arcwise" / "arcwise_plat_full_with_diff.json"  # uiuc-kang-lab/text_to_sql_benchmarks

# --- Machine -------------------------------------------------------------------------------------------------
# Set BAA_MACHINE and BAA_OLLAMA_URL on another machine instead of editing this file, e.g. on the RTX 4080 PC:
#   set BAA_MACHINE=pc-rtx4080
# The machine name becomes part of every result file name, so results from both machines can sit side by side.
MACHINE = os.environ.get("BAA_MACHINE", "server-rtx5060ti")
OLLAMA_URL = os.environ.get("BAA_OLLAMA_URL", "http://localhost:11434")
HARDWARE = {
    "server-rtx5060ti": "NVIDIA RTX 5060 Ti 16 GB, Intel i7-8700K, 48 GB RAM, Linux (openSUSE)",
    "pc-rtx4080": "NVIDIA RTX 4080 16 GB, Windows",
}

# --- Models --------------------------------------------------------------------------------------------------
# think: Qwen3.8 thinks unless every request says "think": false (tested 07.10.); qwen2.5-coder has no thinking.
MODELS = {
    "qwen2.5-coder:7b": {
        "think": None,
        "weights": "Ollama library qwen2.5-coder:7b (Q4_K_M)",
    },
    "qwen2.5-coder:14b": {
        "think": None,
        "weights": "Ollama library qwen2.5-coder:14b (Q4_K_M), digest 9ec8897f747e",
    },
    "baa-qwen3.8-27b": {
        "think": False,
        "weights": "Unsloth Qwen3.8-27B-GGUF UD-Q3_K_XL, sha256 00cf92e6...71e2 (Modelfile in models/)",
    },
}
SMALL_MODEL = "qwen2.5-coder:7b"
MID_MODEL = "qwen2.5-coder:14b"
MAIN_MODEL = "baa-qwen3.8-27b"
EMBEDDING_MODEL = "bge-m3"  # Stage 2 to 4 retrieval

# --- Generation and evaluation -------------------------------------------------------------------------------
GENERATION_OPTIONS = {
    "temperature": 0,  # greedy decoding, so a run is repeatable
    "seed": 42,
    "num_ctx": 8192,  # longest prompt (european_football_2 schema + 3 examples) is about 3,500 tokens
    "num_predict": 1024,  # cap so a model that never stops cannot block a run
}
SQL_TIMEOUT_SECONDS = 30  # one slow query must not hang the run
MAX_RESULT_ROWS = 1_000_000  # a runaway join could otherwise fill the RAM; the largest gold result is far below
TOP_K_EXAMPLES = 3  # Cook et al. 2025: at most three similar question/SQL pairs
