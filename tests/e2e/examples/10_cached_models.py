"""platform-cached model weights: staged on the host before the worker starts.

    rp dev tests/e2e/examples/10_cached_models.py --once
"""

import runpod
from runpod import App, Model

app = App("ex-models")

tiny = Model("sshleifer/tiny-gpt2")


@app.queue(gpu="4090", model=tiny, dependencies=["transformers"])
def generate(prompt: str):
    # weights are already on disk; no download happens here
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(str(tiny.path))
    model = AutoModelForCausalLM.from_pretrained(str(tiny.path))
    inputs = tokenizer(prompt, return_tensors="pt")
    out = model.generate(**inputs, max_new_tokens=8)
    text = tokenizer.decode(out[0])
    print(f"generated: {text!r}")
    return {"path": str(tiny.path), "text": text}


@runpod.local_entrypoint
def main():
    result = generate.remote("hello")
    print("result:", result)
    assert result["path"].startswith("/runpod/model-store/huggingface/")
    assert result["text"]
