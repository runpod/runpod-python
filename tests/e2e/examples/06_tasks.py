"""tasks: one ephemeral pod per call, terminated when done.

    rp dev tests/e2e/examples/06_tasks.py --once
"""

import runpod
from runpod import App

app = App("ex-tasks")


@app.task(cpu="cpu3c-1-2")
def multiply(x: int, y: int):
    print(f"multiplying {x} * {y} on a dedicated pod")
    return x * y


@app.task(gpu="4090")
def gpu_task():
    import torch

    print("checking cuda on a task pod")
    return {"cuda": torch.cuda.is_available(), "torch": str(torch.__version__)}


@runpod.local_entrypoint
def main():
    assert multiply.remote(6, 7) == 42
    info = gpu_task.remote()
    print("gpu task:", info)
    assert info["cuda"] is True
