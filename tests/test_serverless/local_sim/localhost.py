import random
import uvicorn
from fastapi import FastAPI, Request
from typing import Dict, Any
from faker import Faker


fake = Faker()
app = FastAPI()


def generate_fake_job() -> Dict[str, Any]:
    delay = fake.random_digit_above_two()
    return {
        "id": fake.uuid4(),
        "input": fake.sentence(),
        "mock_delay": delay,
    }


@app.get("/v2/{endpoint_id}/job-take/{worker_id}")
async def job_take(endpoint_id: str, worker_id: str):
    """Accept GET request and return a random fake job as a dict"""
    return generate_fake_job()


@app.get("/v2/{endpoint_id}/job-take-batch/{worker_id}")
async def job_take_batch(endpoint_id: str, worker_id: str, batch_size: int = 5):
    """Accept GET request and return a random fake list of jobs"""
    return [generate_fake_job() for _ in range(random.randint(1, batch_size))]


@app.post("/v2/{endpoint_id}/job-done/{worker_id}")
async def job_done(request: Request, endpoint_id: str, worker_id: str):
    """Accept POST request and return the payload posted"""
    payload = await request.json()
    return payload


@app.get("/v2/{endpoint_id}/ping/{worker_id}")
async def ping_worker(endpoint_id: str, worker_id: str):
    """Accept GET request and return ping response with extracted path values"""
    return {"status": "pong"}


if __name__ == "__main__":
    # Run with: python filename.py
    # Or use: uvicorn filename:app --reload
    uvicorn.run(app, host="0.0.0.0", port=8080)
