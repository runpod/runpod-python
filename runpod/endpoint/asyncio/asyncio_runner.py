""" Module for running endpoints asynchronously. """

# pylint: disable=too-few-public-methods,R0801

import asyncio
from typing import Any, Dict

from runpod.endpoint.helpers import FINAL_STATES, is_completed
from runpod.http_client import ClientSession


class Job:
    """Class representing a job for an asynchronous endpoint"""

    def __init__(self, endpoint_id: str, job_id: str, session: ClientSession):
        from runpod import (  # pylint: disable=import-outside-toplevel,cyclic-import
            api_key,
            endpoint_url_base,
        )

        self.endpoint_id = endpoint_id
        self.job_id = job_id
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "X-Request-ID": job_id,
        }
        self.session = session
        self.endpoint_url_base = endpoint_url_base

        self.job_status = None
        self.job_output = None

    async def _fetch_job(self, source: str = "status") -> Dict[str, Any]:
        """Returns the raw json of the status, reaises an exception if invalid.

        Args:
            source: The URL source path of the job status.
        """
        status_url = (
            f"{self.endpoint_url_base}/{self.endpoint_id}/{source}/{self.job_id}"
        )
        job_state = await self.session.get(status_url, headers=self.headers)
        job_state = await job_state.json()

        if is_completed(job_state["status"]):
            self.job_status = job_state["status"]
            self.job_output = job_state.get("output", None)

        return job_state

    async def status(self) -> str:
        """Gets jobs' status

        Returns:
            COMPLETED, FAILED or IN_PROGRESS
        """
        if self.job_status is not None:
            return self.job_status

        job_state = await self._fetch_job()
        return job_state["status"]

    async def _wait_for_completion(self):
        while not is_completed(await self.status()):
            await asyncio.sleep(1)

    async def output(self, timeout: int = 0) -> Any:
        """Waits for serverless API job to complete or fail

        Returns:
            Output of job
        Raises:
            KeyError if job Failed
        """
        if self.job_output is not None:
            return self.job_output

        try:
            await asyncio.wait_for(self._wait_for_completion(), timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError("Job timed out.") from exc

        job_data = await self._fetch_job()
        return job_data.get("output", None)

    async def stream(self) -> Any:
        """Returns a generator that yields the output of the job request."""
        while True:
            await asyncio.sleep(1)
            stream_partial = await self._fetch_job(source="stream")
            if stream_partial["status"] not in FINAL_STATES:
                for chunk in stream_partial.get("stream", []):
                    yield chunk["output"]

    async def cancel(self) -> dict:
        """Cancels current job

        Returns:
            Output of cancel operation
        """
        cancel_url = f"{self.endpoint_url_base}/{self.endpoint_id}/cancel/{self.job_id}"
        async with self.session.post(cancel_url, headers=self.headers) as resp:
            return await resp.json()


class Endpoint:
    """Class for running endpoint"""

    def __init__(self, endpoint_id: str, session: ClientSession):
        from runpod import (
            api_key,  # pylint: disable=import-outside-toplevel
            endpoint_url_base,
        )

        self.endpoint_id = endpoint_id
        self.endpoint_url = f"{endpoint_url_base}/{self.endpoint_id}/run"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        self.session = session

    async def run(self, endpoint_input: dict) -> Job:
        """Runs endpoint with specified input

        Args:
            endpoint_input: any dictionary with input

        Returns:
            Newly created job
        """
        async with self.session.post(
            self.endpoint_url, headers=self.headers, json={"input": endpoint_input}
        ) as resp:
            json_resp = await resp.json()

        return Job(self.endpoint_id, json_resp["id"], self.session)

    async def health(self) -> dict:
        """Checks health of endpoint

        Returns:
            Health of endpoint
        """
        async with self.session.get(
            f"{self.endpoint_id}/health", headers=self.headers
        ) as resp:
            return await resp.json()

    async def purge_queue(self) -> dict:
        """Purges queue of endpoint

        Returns:
            Purge status
        """
        async with self.session.post(
            f"{self.endpoint_id}/purge", headers=self.headers
        ) as resp:
            return await resp.json()
