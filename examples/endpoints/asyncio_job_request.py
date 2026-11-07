"""
Example of calling an endpoint using asyncio.
"""

import asyncio

import runpod
from runpod import AsyncioEndpoint, AsyncioJob, http_client

# Run `rp login` once to store your API key, or uncomment the line below:
# runpod.api_key = "YOUR_RUNPOD_API_KEY"

JOB_TIMEOUT = 120  # seconds


async def main():
    """
    Function to run the example.
    """
    async with http_client.AsyncClientSession() as session:
        # Invoke API
        payload = {}
        endpoint = AsyncioEndpoint("YOUR_ENDPOINT_ID", session)
        job: AsyncioJob = await endpoint.run(payload)

        # Get current job status
        status = await job.status()

        # Print status
        print(status)

        # Wait until the job completes or fails, up to JOB_TIMEOUT seconds.
        # Raises TimeoutError if the job is still running after that.
        output = await job.output(timeout=JOB_TIMEOUT)

        # Print output
        print(output)


asyncio.run(main())
