"""streaming permutations: sync/async generators, every consumption style.

    rp dev tests/e2e/examples/13_streaming.py --once
"""

import asyncio

import runpod
from runpod import App

app = App("ex-stream")


@app.queue(cpu="cpu3c-1-2")
def count_up(n: int):
    for i in range(n):
        yield {"i": i}


@app.queue(cpu="cpu3c-1-2")
async def count_down(n: int):
    for i in range(n, 0, -1):
        yield i


@runpod.local_entrypoint
def main():
    # sync iteration over a sync generator
    chunks = list(count_up.stream(3))
    assert chunks == [{"i": 0}, {"i": 1}, {"i": 2}], chunks

    # async iteration over an async generator
    async def consume():
        return [c async for c in count_down.stream.aio(3)]

    chunks = asyncio.run(consume())
    assert chunks == [3, 2, 1], chunks

    # .remote() on a generator aggregates every chunk
    all_chunks = count_up.remote(2)
    assert all_chunks == [{"i": 0}, {"i": 1}], all_chunks

    # spawn -> reconnect-style streaming from the job handle
    job = count_up.spawn(2)
    chunks = list(job.stream())
    assert chunks == [{"i": 0}, {"i": 1}], chunks
    assert job.result() == [{"i": 0}, {"i": 1}]

    # .local() still yields directly, no cloud involved
    assert list(count_up.local(2)) == [{"i": 0}, {"i": 1}]

    print("all streaming styles ok")


if __name__ == "__main__":
    main()
