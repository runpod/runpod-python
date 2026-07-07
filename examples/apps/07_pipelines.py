"""functions calling functions: workers invoke sibling resources remotely.

    rp dev examples/apps/07_pipelines.py --once
"""

import runpod
from runpod import App

app = App("ex-pipeline")


@app.queue(cpu="cpu3c-1-2")
def tokenize(text: str):
    tokens = text.split()
    print(f"tokenized into {len(tokens)} tokens")
    return tokens


@app.queue(cpu="cpu3c-1-2")
def count(tokens: list):
    return len(tokens)


@app.queue(cpu="cpu3c-1-2")
def pipeline(text: str):
    # nested .remote() calls run on the sibling resources
    tokens = tokenize.remote(text)
    total = count.remote(tokens)
    return {"tokens": tokens, "total": total}


@runpod.local_entrypoint
def main():
    result = pipeline.remote("the quick brown fox")
    print("pipeline:", result)
    assert result["total"] == 4
    assert result["tokens"][0] == "the"
