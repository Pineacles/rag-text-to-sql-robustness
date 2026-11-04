"""Talking to Ollama: generate an answer, embed texts, and read what is loaded on the GPU.

Connection errors are not caught on purpose: the run stops, nothing wrong is written, and a restart resumes.
"""

import time

import requests

import config

GENERATE_TIMEOUT_SECONDS = 900  # the first request also loads the model, which can take a while


def generate(model, prompt):
    """One completion with the fixed generation options; returns the answer text plus token counts and time."""
    body = {"model": model, "prompt": prompt, "stream": False, "options": config.GENERATION_OPTIONS}
    think = config.MODELS[model]["think"]
    if think is not None:
        body["think"] = think
    started = time.time()
    response = requests.post(f"{config.OLLAMA_URL}/api/generate", json=body, timeout=GENERATE_TIMEOUT_SECONDS)
    response.raise_for_status()
    result = response.json()
    return {
        "raw_answer": result["response"],
        "seconds": round(time.time() - started, 3),
        "prompt_tokens": result.get("prompt_eval_count"),
        "output_tokens": result.get("eval_count"),
        "done_reason": result.get("done_reason"),
    }


def load_model(model):
    """Load the model into VRAM before timing starts, so the first question's latency is not the load time.

    An empty prompt only loads the model; the same options are sent so Ollama does not reload it for num_ctx.
    """
    body = {"model": model, "prompt": "", "stream": False, "options": config.GENERATION_OPTIONS}
    response = requests.post(f"{config.OLLAMA_URL}/api/generate", json=body, timeout=GENERATE_TIMEOUT_SECONDS)
    response.raise_for_status()


def embed(texts):
    """Embedding vectors for a list of texts (used for retrieval in Stage 2 to 4)."""
    body = {"model": config.EMBEDDING_MODEL, "input": texts}
    response = requests.post(f"{config.OLLAMA_URL}/api/embed", json=body, timeout=GENERATE_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()["embeddings"]


def model_details(model):
    """Digest and quantization label Ollama reports for a model, so the report says exactly what ran."""
    response = requests.post(f"{config.OLLAMA_URL}/api/show", json={"model": model}, timeout=60)
    response.raise_for_status()
    details = response.json().get("details", {})
    tags = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=60).json()["models"]
    digest = next((m["digest"] for m in tags if m["name"] in (model, f"{model}:latest")), None)
    return {
        "digest": digest,
        "quantization_label": details.get("quantization_level"),
        "parameter_size": details.get("parameter_size"),
    }


def gpu_share(model):
    """Share of the loaded model that sits in VRAM (1.0 = fully on the GPU, less = spilled to the CPU)."""
    loaded = requests.get(f"{config.OLLAMA_URL}/api/ps", timeout=60).json()["models"]
    for entry in loaded:
        if entry["name"] in (model, f"{model}:latest"):
            return {"vram_share": round(entry["size_vram"] / entry["size"], 3), "context": entry.get("context_length")}
    return {"vram_share": None, "context": None}


def ollama_version():
    return requests.get(f"{config.OLLAMA_URL}/api/version", timeout=60).json()["version"]
