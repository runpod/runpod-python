""" Helper functions for the Runpod Endpoint API. """

from typing import Optional

from runpod.error import JobError

FINAL_STATES = ["COMPLETED", "FAILED", "TIMED_OUT"]

# Exception Messages
UNAUTHORIZED_MSG = "401 Unauthorized | Make sure Runpod API key is set and valid."
API_KEY_NOT_SET_MSG = (
    "Expected `run_pod.api_key` to be initialized. "
    "You can solve this by setting `run_pod.api_key = 'your-key'. "
    "An API key can be generated at "
    "https://console.runpod.io/user/settings"
)


def is_completed(status: str) -> bool:
    """Returns true if status is one of the possible final states for a serverless request."""
    return status in ["COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"]


def raise_for_job_state(job_id: str, status: str, error: Optional[str] = None) -> None:
    """Raise JobError when a job ended in a final state other than COMPLETED."""
    if status != "COMPLETED" and is_completed(status):
        raise JobError(job_id, status, error)
