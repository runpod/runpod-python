import asyncio
import math
from faker import Faker
from typing import Any, Dict, Optional, List

from runpod.serverless.modules.rp_scale import JobScaler, RunPodLogger, JobsProgress

fake = Faker()
log = RunPodLogger()
job_progress = JobsProgress()


# Change this number to your desired concurrency
start = 1


# sample concurrency modifier that loops
def collatz_conjecture(current_concurrency):
    if current_concurrency == 1:
        return start

    if current_concurrency % 2 == 0:
        return math.floor(current_concurrency / 2)
    else:
        return current_concurrency * 3 + 1


def fake_job():
    # Change this number to your desired delay
    delay = fake.random_digit_above_two()
    return {
        "id": fake.uuid4(),
        "input": fake.sentence(),
        "mock_delay": delay,
    }


async def fake_get_job(session, num_jobs: int = 1) -> Optional[List[Dict[str, Any]]]:
    # Change this number to your desired delay
    delay = fake.random_digit_above_two() - 1

    log.info(f"... artificial delay ({delay}s)")
    await asyncio.sleep(delay)  # Simulates a blocking process

    jobs = [fake_job() for _ in range(num_jobs)]
    log.info(f"... Generated # jobs: {len(jobs)}")
    return jobs


async def fake_handle_job(session, config, job) -> dict:
    await asyncio.sleep(job["mock_delay"])  # Simulates a blocking process
    log.info(f"... Job handled ({job['mock_delay']}s)", job["id"])


job_scaler = JobScaler(
    {
        # "concurrency_modifier": collatz_conjecture,
        # "jobs_fetcher_timeout": 5,
        "jobs_fetcher": fake_get_job,
        "jobs_handler": fake_handle_job,
    }
)
job_scaler.start()
