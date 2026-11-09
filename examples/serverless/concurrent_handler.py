"""Concurrency Modifier Example

An async handler lets one worker run several jobs at once. The worker calls
the concurrency modifier with its current concurrency and resizes to the value
it returns, waiting until no jobs are in flight before resizing.

To test locally, run:
python concurrent_handler.py --test_input '{"input": {"seconds": 1}}'
"""

import asyncio

import runpod

MAX_CONCURRENCY = 4


# ---------------------------------- Handler --------------------------------- #
async def async_handler(job):
    """
    Async handler. While it awaits, the worker can start other jobs.
    """
    seconds = job["input"].get("seconds", 1)
    await asyncio.sleep(seconds)
    return {"slept": seconds}


# --------------------------- Concurrency Modifier --------------------------- #
def concurrency_modifier(current_concurrency: int) -> int:
    """
    Return the number of jobs this worker should run at once.

    Replace the fixed value with your own logic, for example based on free
    GPU memory or queue depth.
    """
    return MAX_CONCURRENCY


runpod.serverless.start(
    {"handler": async_handler, "concurrency_modifier": concurrency_modifier}
)
