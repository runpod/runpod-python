"""gpu selection: shorthand names resolve to real devices.

    rp dev tests/e2e/examples/03_gpu.py --once
"""

import runpod
from runpod import App

app = App("ex-gpu")


@app.queue(gpu="4090", env={"MODE": "example"})
def cuda_info(prompt: str):
    import os

    import torch

    print(f"prompt: {prompt}")
    return {
        "cuda": torch.cuda.is_available(),
        "device": torch.cuda.get_device_name(0),
        "torch": str(torch.__version__),
        "mode": os.environ.get("MODE"),
    }


@runpod.local_entrypoint
def main():
    info = cuda_info.remote("hello gpu")
    print("info:", info)
    assert info["cuda"] is True
    assert "4090" in info["device"]
    assert info["mode"] == "example"
