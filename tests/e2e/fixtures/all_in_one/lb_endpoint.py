import os

from runpod_flash import Endpoint, GpuType, PodTemplate

branch = os.environ.get("RUNPOD_PYTHON_BRANCH", "main")

template = PodTemplate(
    startScript=(
        f"pip install git+https://github.com/runpod/runpod-python@{branch} "
        f"--no-cache-dir --force-reinstall --no-deps"
    ),
)

config = Endpoint(
    name="lb-worker",
    gpu=GpuType.NVIDIA_GEFORCE_RTX_4090,
    template=template,
)


@config.post("/echo")
async def echo(text: str) -> dict:
    return {"echoed": text}
