"""drive the deployed e2e matrix. run AFTER `rp deploy tests/e2e/matrix`.

    RUNPOD_RUNTIME_TAG=dev python tests/e2e/matrix/run.py [--only q-basic,...]

each check is independent; failures are collected, not fatal, so one
broken permutation doesn't hide the rest.
"""

import argparse
import asyncio
import sys
import time
import traceback

from main import (  # noqa: E402
    Svc,
    app,
    q_basic,
    q_caller,
    q_custom,
    q_deps,
    q_gpu,
    q_stream,
    q_stream_async,
    t_gen,
    t_gpu,
    t_mul,
)


async def check_q_basic():
    r = await q_basic.remote.aio(21)
    assert r == {"doubled": 42}, r
    return r


async def check_q_deps():
    r = await q_deps.remote.aio("hi")
    assert r["pyfiglet"], r
    assert r["jq"] is True, r
    return {k: r[k] for k in ("pyfiglet", "jq")}


async def check_q_custom():
    r = await q_custom.remote.aio(1_500_000)
    assert r == {"human": "1.5 million"}, r
    return r


async def check_q_gpu():
    r = await q_gpu.remote.aio()
    assert r["cuda"] is True, r
    return r


async def check_q_caller():
    r = await q_caller.remote.aio(3)
    assert r["from_queue"] == {"doubled": 6}, r
    assert r["from_task"] == {"product": 30}, r
    return r


async def check_t_mul():
    r = await t_mul.remote.aio(6, 7)
    assert r == {"product": 42}, r
    return r


async def check_t_gpu():
    r = await t_gpu.remote.aio(64)
    assert "device" in r, r
    return r


async def check_api():
    r1 = await Svc.post.aio("/bump", {"by": 5})
    assert r1["counter"] >= 5 and r1["ready"] is True, r1
    r2 = await Svc.get.aio("/stats")
    assert r2["counter"] >= 5, r2
    return {"bump": r1, "stats": r2}


async def check_api_streaming_response():
    # api routes may stream; the client delivers the body intact
    r = await Svc.get.aio("/tokens")
    assert r == "alpha\nbeta\ngamma\n", repr(r)
    return {"body": r}


async def check_spawn():
    job = await q_basic.spawn.aio(4)
    r = await job.result.aio()
    assert r == {"doubled": 8}, r
    return r


async def check_job_ops():
    job = await q_basic.spawn.aio(5)
    status = await job.status.aio()
    assert status, status
    r = await job.result.aio()
    assert r == {"doubled": 10}, r
    # reconnect by id and read the terminal status
    reconnected = await q_basic.job.aio(job.id)
    status = await reconnected.status.aio()
    assert status == "COMPLETED", status
    return {"id": job.id, "status": status}


async def check_stream():
    chunks = [c async for c in q_stream.stream.aio(3)]
    assert chunks == [{"i": 0}, {"i": 1}, {"i": 2}], chunks
    return chunks


async def check_stream_async_gen():
    chunks = [c async for c in q_stream_async.stream.aio(3)]
    assert chunks == [3, 2, 1], chunks
    return chunks


async def check_stream_aggregate():
    r = await q_stream.remote.aio(2)
    assert r == [{"i": 0}, {"i": 1}], r
    return r


async def check_stream_from_job():
    job = await q_stream.spawn.aio(2)
    chunks = [c async for c in job.stream.aio()]
    assert chunks == [{"i": 0}, {"i": 1}], chunks
    return chunks


async def check_task_generator_aggregates():
    r = await t_gen.remote.aio(4)
    assert r == [0, 1, 4, 9], r
    return r


async def check_task_stream_rejected():
    from runpod.apps.errors import InvalidResourceError

    try:
        async for _ in t_gen.stream.aio(2):
            pass
    except InvalidResourceError as exc:
        assert "do not stream" in str(exc), exc
        return {"rejected": True}
    raise AssertionError("task .stream() should raise InvalidResourceError")


CHECKS = {
    "q-basic": check_q_basic,
    "q-deps": check_q_deps,
    "q-custom": check_q_custom,
    "q-gpu": check_q_gpu,
    "q-caller": check_q_caller,
    "t-mul": check_t_mul,
    "t-gpu": check_t_gpu,
    "api": check_api,
    "spawn": check_spawn,
    "job-ops": check_job_ops,
    "stream": check_stream,
    "stream-async-gen": check_stream_async_gen,
    "stream-aggregate": check_stream_aggregate,
    "stream-from-job": check_stream_from_job,
    "task-gen-aggregate": check_task_generator_aggregates,
    "task-stream-rejected": check_task_stream_rejected,
    "api-streaming-response": check_api_streaming_response,
}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="comma-separated check names")
    args = parser.parse_args()

    names = args.only.split(",") if args.only else list(CHECKS)
    results, failures = {}, {}

    for name in names:
        started = time.monotonic()
        print(f"--- {name} ...", flush=True)
        try:
            results[name] = await CHECKS[name]()
            elapsed = time.monotonic() - started
            print(f"    PASS {elapsed:.1f}s  {results[name]}", flush=True)
        except Exception as exc:  # noqa: BLE001 - collect and report
            elapsed = time.monotonic() - started
            failures[name] = exc
            print(f"    FAIL {elapsed:.1f}s  {exc}", flush=True)
            traceback.print_exc()

    print(f"\n{len(results)}/{len(names)} passed")
    if failures:
        print(f"failed: {', '.join(failures)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
