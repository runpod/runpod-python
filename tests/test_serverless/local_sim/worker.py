import asyncio
import math
from faker import Faker
from runpod.serverless.modules.rp_scale import JobScaler, RunPodLogger
from runpod.serverless.modules.rp_ping import Heartbeat
from runpod.serverless.modules.worker_state import JobsProgress


fake = Faker()
logger = RunPodLogger()
heartbeat = Heartbeat()

start = 3


# sample concurrency modifier that loops
def collatz_conjecture(current_concurrency):
    if current_concurrency == 1:
        return start

    if current_concurrency % 2 == 0:
        return math.floor(current_concurrency / 2)
    else:
        return current_concurrency * 3 + 1


async def fake_handle_job(job):
    await asyncio.sleep(job["mock_delay"])  # Simulates a blocking process
    logger.info(f"Job handled ({job['mock_delay']}s): `{job['input']}`", job["id"])
    return job["input"]


job_scaler = JobScaler(
    {
        "concurrency_modifier": collatz_conjecture,
        "handler": fake_handle_job,
    }
)

if __name__ == "__main__":
    JobsProgress().clear()
    heartbeat.start_ping()
    job_scaler.start()
