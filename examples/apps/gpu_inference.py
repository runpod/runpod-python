"""Run a function on a GPU.

Ask for hardware by name — "4090", "H100", "B200" all work, as do
pool ids like GpuGroup.ADA_24 and exact device names. Dependencies
listed on the decorator are ready before your function runs, so the
worker needs no custom image.

    rp dev examples/apps/gpu_inference.py
"""

import runpod
from runpod import App

app = App("gpu-demo")


@app.queue(gpu="4090", dependencies=["numpy"])
def matmul_benchmark(size: int = 4096):
    import time

    import torch

    device = torch.cuda.get_device_name(0)
    print(f"running on {device}")

    a = torch.randn(size, size, device="cuda")
    b = torch.randn(size, size, device="cuda")

    torch.cuda.synchronize()
    start = time.time()
    for _ in range(10):
        a @ b
    torch.cuda.synchronize()
    elapsed = time.time() - start

    tflops = 10 * 2 * size**3 / elapsed / 1e12
    print(f"{size}x{size} matmul: {tflops:.1f} TFLOPS")
    return {"device": device, "tflops": round(tflops, 1)}


@runpod.local_entrypoint
def main():
    result = matmul_benchmark.remote()
    print("benchmark:", result)
