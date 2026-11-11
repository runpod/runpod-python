"""Simulated job-take benchmark for JobScaler take-ahead (#647).

Runs real JobScaler instances against an in-process fake job queue that
models the latency reported in #647: a job-take sent while jobs are queued
costs a flat ~0.5 s however many jobs it asks for, while a take waiting on
an empty queue returns ~50 ms after a job arrives.

This is a model, not a measurement of the platform. It isolates the SDK's
take/run scheduling so main and take-ahead can be compared under the same
assumed latencies.

    uv run python scripts/benchmark_job_take.py
    uv run python scripts/benchmark_job_take.py --reps 7 --take-ahead 32
"""

import argparse
import asyncio
import statistics
import time

from runpod.serverless.modules import rp_scale


class Progress:
    """Per-worker stand-in for the JobsProgress singleton."""

    def __init__(self):
        self.ids = set()

    def add(self, job):
        self.ids.add(job["id"])

    def remove(self, job):
        self.ids.discard(job["id"])

    def get_job_count(self):
        return len(self.ids)


class FakeQueue:
    """Platform-side request queue shared by all simulated workers."""

    def __init__(self, take_latency, idle_latency):
        self.take_latency = take_latency
        self.idle_latency = idle_latency
        self.pending = []
        self.arrived = asyncio.Event()
        self.taken_by = {}

    def submit(self, count, start=0):
        self.pending.extend(f"job-{start + i}" for i in range(count))
        self.arrived.set()

    async def take(self, worker, count):
        if self.pending:
            await asyncio.sleep(self.take_latency)
        else:
            self.arrived.clear()
            try:
                await asyncio.wait_for(self.arrived.wait(), timeout=5)
            except asyncio.TimeoutError:
                return []
            await asyncio.sleep(self.idle_latency)
        jobs, self.pending = self.pending[:count], self.pending[count:]
        for job_id in jobs:
            self.taken_by[job_id] = worker
        return [{"id": job_id, "input": {}} for job_id in jobs]


async def run_burst(args, take_ahead, workers, burst, run_seconds):
    queue = FakeQueue(args.take_latency, args.idle_latency)
    done_at = []
    start = time.monotonic()
    scalers, tasks = [], []

    for worker in range(workers):

        async def fetcher(_session, count, worker=worker):
            return await queue.take(worker, count)

        async def handler(_session, _config, _job):
            await asyncio.sleep(run_seconds)
            done_at.append(time.monotonic() - start)

        scaler = rp_scale.JobScaler(
            {
                "handler": lambda _: None,
                "jobs_fetcher": fetcher,
                "take_ahead": take_ahead,
            }
        )
        scaler.jobs_handler = handler
        scaler.job_progress = Progress()
        scaler.current_concurrency = args.concurrency
        scalers.append(scaler)
        tasks += [
            asyncio.create_task(scaler.get_jobs(None)),
            asyncio.create_task(scaler.run_jobs(None)),
        ]

    await asyncio.sleep(0.2)  # every worker is long-polling an empty queue
    start = time.monotonic()
    queue.submit(burst)
    while len(done_at) < burst:
        await asyncio.sleep(0.01)

    for scaler in scalers:
        scaler.kill_worker()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    done_at.sort()
    per_worker = [list(queue.taken_by.values()).count(w) for w in range(workers)]
    return done_at[int(0.99 * burst) - 1], per_worker


def report(args, label, workers, burst, run_seconds):
    print(
        f"\n{label}: {workers} worker(s) x {args.concurrency} slots, "
        f"burst of {burst}, {run_seconds}s per job, {args.reps} reps"
    )
    print(
        f"{'take_ahead':>10} | {'p99 done (median)':>17} | {'runs':<34} | jobs per worker"
    )
    for take_ahead in (0, args.take_ahead):
        results = [
            asyncio.run(run_burst(args, take_ahead, workers, burst, run_seconds))
            for _ in range(args.reps)
        ]
        times = [r[0] for r in results]
        runs = ", ".join(f"{t:.2f}" for t in times)
        print(
            f"{take_ahead:>10} | {statistics.median(times):>16.2f}s | {runs:<34} | {results[-1][1]}"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--take-ahead", type=int, default=32)
    parser.add_argument("--take-latency", type=float, default=0.5)
    parser.add_argument("--idle-latency", type=float, default=0.05)
    args = parser.parse_args()
    print(
        f"take latency {args.take_latency}s with jobs queued, "
        f"{args.idle_latency}s after an idle long-poll"
    )

    # The #647 setup: one saturated vLLM-like worker draining a deep queue.
    report(args, "deep queue", 1, 512, 0.5)
    # Jobs slower than a take: holding jobs saves less.
    report(args, "slow jobs", 1, 256, 2.0)
    # Deep queue across workers: held jobs could have gone to another worker.
    report(args, "deep queue, 4 workers", 4, 1024, 0.5)
    # Shallow queue across workers: take-ahead must not hoard jobs.
    report(args, "shallow queue", 4, 64, 0.5)


if __name__ == "__main__":
    main()
