"""every way to call a function: remote, async, spawn, local.

    rp dev tests/e2e/examples/02_invocation_styles.py --once
"""

import asyncio

import runpod
from runpod import App

app = App("ex-invoke")


@app.queue(cpu="cpu3c-1-2")
def square(x: int):
    return x * x


@runpod.local_entrypoint
def main():
    # sync remote call, blocks for the result
    assert square.remote(4) == 16

    # async variant, for use inside event loops
    async def go():
        return await square.remote.aio(5)

    assert asyncio.run(go()) == 25

    # fire and forget -> collect later
    job = square.spawn(6)
    assert job.result() == 36

    # run in this process, no cloud involved
    assert square.local(7) == 49

    print("all four invocation styles ok")
