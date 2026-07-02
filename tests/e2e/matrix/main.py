"""e2e matrix app: every resource kind, cross-calls, deps, custom images.

deployed by tests/e2e/matrix/run.py; each resource exercises a distinct
permutation of the surface.
"""

import runpod
from runpod import App

app = App("e2e-matrix")


# -- queue permutations ------------------------------------------------

@app.queue(name="q-basic", cpu="cpu3c-1-2", workers=(0, 1))
def q_basic(x: int):
    return {"doubled": x * 2}


@app.queue(
    name="q-deps",
    cpu="cpu3c-2-4",
    workers=(0, 1),
    dependencies=["pyfiglet"],
    system_dependencies=["jq"],
)
def q_deps(word: str):
    import shutil
    import pyfiglet

    return {
        "art": pyfiglet.figlet_format(word).splitlines()[0],
        "pyfiglet": pyfiglet.__version__,
        "jq": shutil.which("jq") is not None,
    }


@app.queue(
    name="q-custom",
    cpu="cpu3c-2-4",
    workers=(0, 1),
    image="python:3.12-slim",
    dependencies=["humanize"],
)
def q_custom(n: int):
    import humanize

    return {"human": humanize.intword(n)}


@app.queue(name="q-gpu", gpu=runpod.GpuGroup.ADA_24, workers=(0, 1))
def q_gpu():
    import torch

    return {
        "torch": torch.__version__,
        "cuda": torch.cuda.is_available(),
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
    }


# -- cross-calls -------------------------------------------------------

@app.queue(name="q-caller", cpu="cpu3c-2-4", workers=(0, 1))
async def q_caller(x: int):
    """queue worker fanning out to another queue and a task."""
    doubled = await q_basic.remote.aio(x)
    product = await t_mul.remote.aio(x, 10)
    return {"from_queue": doubled, "from_task": product}


# -- tasks -------------------------------------------------------------

@app.task(name="t-mul", cpu="cpu3c-1-2")
def t_mul(a: int, b: int):
    return {"product": a * b}


@app.task(name="t-gpu", gpu=runpod.GpuGroup.ADA_24)
def t_gpu(size: int):
    import torch

    m = torch.rand(size, size, device="cuda")
    return {"trace": float(m.trace()), "device": torch.cuda.get_device_name(0)}


# -- api (load-balanced) -----------------------------------------------

@app.api(name="a-svc", cpu="cpu3c-2-4", workers=(1, 1))
class Svc:
    @runpod.init
    def setup(self):
        self.counter = 0
        self.ready = True

    @runpod.post("/bump")
    def bump(self, body: dict):
        self.counter += int(body.get("by", 1))
        return {"counter": self.counter, "ready": self.ready}

    @runpod.get("/stats")
    def stats(self):
        return {"counter": self.counter}
