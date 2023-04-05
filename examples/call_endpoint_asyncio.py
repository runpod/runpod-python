"""
Example of calling an endpoint using asyncio.
"""

import runpod
from runpod import AsyncioEndpoint, AsyncioJob
import aiohttp
import asyncio

asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # For Windows Users

runpod.api_key = "YOUR_API_KEY"


async def main():
    '''
    Function to run the example.
    '''
    async with aiohttp.ClientSession() as session:
        # Invoke API
        payload = {}
        endpoint = AsyncioEndpoint("ENDPOINT_ID", session)
        job: AsyncioJob = await endpoint.run(payload)

        # Get current job status
        status = await job.status()

        # Print status
        print(status)

        # Wait until job is completed or failed
        output = await job.output()

        # Print output
        print(output)

asyncio.run(main())
