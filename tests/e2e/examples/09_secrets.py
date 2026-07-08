"""platform secrets injected as env vars, decrypted only on the worker.

requires the secret to exist first:

    rp secret add ex-demo-secret --value "s3cret"
    rp dev tests/e2e/examples/09_secrets.py --once
"""

import runpod
from runpod import App, Secret

app = App("ex-secrets")


@app.queue(cpu="cpu3c-1-2", env={"DEMO_TOKEN": Secret("ex-demo-secret"), "MODE": "example"})
def peek():
    import os

    token = os.environ.get("DEMO_TOKEN", "")
    # never print secrets; report shape only
    return {
        "mode": os.environ.get("MODE"),
        "token_present": bool(token),
        "token_length": len(token),
    }


@runpod.local_entrypoint
def main():
    result = peek.remote()
    print("peek:", result)
    assert result["token_present"] is True
    assert result["mode"] == "example"
