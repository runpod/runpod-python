'''
Author: Oleg Rybalko
Github: https://github.com/SkullMag
Date: 2023-03-27
'''
# pylint: disable=too-few-public-methods,R0801

import asyncio
import aiohttp


class Job:
    """Class representing a job for an asynchronous endpoint"""

    def __init__(self, endpoint_id: str, job_id: str, session: aiohttp.ClientSession):
        from runpod import api_key, endpoint_url_base  # pylint: disable=import-outside-toplevel,cyclic-import

        self.endpoint_id = endpoint_id
        self.job_id = job_id
        self.status_url = f"{endpoint_url_base}/{self.endpoint_id}/status/{self.job_id}"
        self.cancel_url = f"{endpoint_url_base}/{self.endpoint_id}/cancel/{self.job_id}"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        self.session = session

    async def status(self) -> str:
        """Gets jobs' status

        Returns:
            COMPLETED, FAILED or IN_PROGRESS
        """
        async with self.session.get(self.status_url, headers=self.headers) as resp:
            return (await resp.json())["status"]

    async def output(self) -> any:
        """Waits for serverless API job to complete or fail

        Returns:
            Output of job
        Raises:
            KeyError if job Failed
        """
        while await self.status() not in ["COMPLETED", "FAILED"]:
            await asyncio.sleep(1)

        async with self.session.get(self.status_url, headers=self.headers) as resp:
            return (await resp.json())["output"]

    async def cancel(self) -> dict:
        """Cancels current job

        Returns:
            Output of cancel operation
        """

        async with self.session.post(self.cancel_url, headers=self.headers) as resp:
            return await resp.json()


class Endpoint:
    """Class for running endpoint"""

    def __init__(self, endpoint_id: str, session: aiohttp.ClientSession):
        from runpod import api_key, endpoint_url_base  # pylint: disable=import-outside-toplevel

        self.endpoint_id = endpoint_id
        self.endpoint_url = f"{endpoint_url_base}/{self.endpoint_id}/run"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
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
