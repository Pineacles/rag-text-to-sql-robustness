"""Speed measurements: bge-m3 embedding time per question, and Qwen3.8-27B latency with think on vs off.

Run while nothing else uses Ollama, else model swaps distort the numbers:
  uv run python -m tools.measure_speed results/measurements/speed_YYYY-MM-DD.json
"""

import json
import statistics
import sys
import time

import requests

import config
from common import bird, prompt

OUT = sys.argv[1]
results = {"embedding": {}, "think": []}


def ps():
    return requests.get(f"{config.OLLAMA_URL}/api/ps", timeout=60).json()["models"]


# --- Embedding speed -----------------------------------------------------------------------------
questions = bird.load_arcwise_questions()
texts = [q["question"] for q in questions[:200]]
url = f"{config.OLLAMA_URL}/api/embed"
requests.post(url, json={"model": config.EMBEDDING_MODEL, "input": ["warm up"]}, timeout=600).raise_for_status()
single = []
for text in texts:
    started = time.perf_counter()
    requests.post(url, json={"model": config.EMBEDDING_MODEL, "input": [text]}, timeout=600).raise_for_status()
    single.append(1000 * (time.perf_counter() - started))
started = time.perf_counter()
for start in range(0, len(texts), 64):
    requests.post(url, json={"model": config.EMBEDDING_MODEL, "input": texts[start:start + 64]}, timeout=600)
batched = 1000 * (time.perf_counter() - started) / len(texts)
loaded = [m for m in ps() if m["name"].startswith(config.EMBEDDING_MODEL)]
results["embedding"] = {
    "n": len(texts),
    "one_per_request_ms_mean": round(statistics.mean(single), 1),
    "one_per_request_ms_median": round(statistics.median(single), 1),
    "batch64_ms_per_question": round(batched, 2),
    "vram_share": round(loaded[0]["size_vram"] / loaded[0]["size"], 3) if loaded else None,
}
print(json.dumps(results["embedding"]), flush=True)

# --- Think on vs off -----------------------------------------------------------------------------
model = config.MAIN_MODEL
# think on needs room for the reasoning: prompt (~1,000 tokens) + up to 6,144 output tokens fits num_ctx 8192
options = {"off": config.GENERATION_OPTIONS, "on": {**config.GENERATION_OPTIONS, "num_predict": 6144}}
gen_url = f"{config.OLLAMA_URL}/api/generate"
body = {"model": model, "prompt": "", "stream": False, "options": options["on"]}
requests.post(gen_url, json=body, timeout=1200).raise_for_status()  # load once, same num_ctx for both modes
for question in questions[:20]:
    text = prompt.build_prompt(question, use_evidence=False, examples=[])
    for mode in ("off", "on"):
        body = {"model": model, "prompt": text, "stream": False, "think": mode == "on", "options": options[mode]}
        started = time.perf_counter()
        answer = requests.post(gen_url, json=body, timeout=3600).json()
        seconds = time.perf_counter() - started
        if "response" not in answer:  # e.g. Ollama aborts a thinking loop: "token repeat limit reached"
            verdict, answer = f"aborted: {answer.get('error')}", {}
        else:
            sql = prompt.extract_sql(answer["response"])
            verdict = bird.judge(question["db_id"], sql, question["SQL"])[0] if sql else "no_sql"
        results["think"].append({
            "question_id": question["question_id"], "mode": mode, "seconds": round(seconds, 2),
            "output_tokens": answer.get("eval_count"), "thinking_chars": len(answer.get("thinking") or ""),
            "done_reason": answer.get("done_reason"), "result": verdict,
            "think_tag_in_response": "</think>" in answer.get("response", ""),
            "raw_answer": answer.get("response"),  # kept so the answers can be re-scored later
        })
        print(json.dumps(results["think"][-1]), flush=True)
loaded = [m for m in ps() if m["name"].startswith(model)]
results["think_vram_share"] = round(loaded[0]["size_vram"] / loaded[0]["size"], 3) if loaded else None
with open(OUT, "w", encoding="utf-8") as handle:
    json.dump(results, handle, indent=1)
