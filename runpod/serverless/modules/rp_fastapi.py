''' Used to launch the FastAPI web server when worker is running in API mode. '''
# pylint: disable=too-few-public-methods

import os
from typing import Union

import uvicorn
from fastapi import FastAPI, APIRouter
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from .rp_handler import is_generator
from .rp_job import run_job, run_job_generator
from .worker_state import Jobs
from .rp_ping import Heartbeat
from ...version import __version__ as runpod_version


RUNPOD_ENDPOINT_ID = os.environ.get("RUNPOD_ENDPOINT_ID", None)

DESCRIPTION = """
This API server is provided as a method of testing and debugging your worker locally.
Additionally, you can use this to test code that will be making requests to your worker.

### Endpoints

The URLs provided are named to match the endpoints that you will be provided when running on RunPod.

---

*Note: When running your worker on the RunPod platform, this API server will not be used.*
"""

job_list = Jobs()

heartbeat = Heartbeat()


# ------------------------------- Input Objects ------------------------------ #
class Job(BaseModel):
    ''' Represents a job. '''
    id: str
    input: Union[dict, list, str, int, float, bool]


class TestJob(BaseModel):
    ''' Represents a test job.
    input can be any type of data.
    '''
    id: str = "test_job"
    input: Union[dict, list, str, int, float, bool]


class WorkerAPI:
    ''' Used to launch the FastAPI web server when the worker is running in API mode. '''

    def __init__(self, handler=None):
        '''
        Initializes the WorkerAPI class.
        1. Starts the heartbeat thread.
        2. Initializes the FastAPI web server.
        3. Sets the handler for processing jobs.
        '''
        # Start the heartbeat thread.
        heartbeat.start_ping()

        # Set the handler for processing jobs.
        self.config = {"handler": handler}

        # Initialize the FastAPI web server.
        self.rp_app = FastAPI(
            title="RunPod | Test Worker | API",
            description=DESCRIPTION,
            version=runpod_version,
        )

        # Create an APIRouter and add the route for processing jobs.
        api_router = APIRouter()

        if RUNPOD_ENDPOINT_ID:
            api_router.add_api_route(f"/{RUNPOD_ENDPOINT_ID}/realtime", self._run, methods=["POST"])

        api_router.add_api_route("/runsync", self._debug_run, methods=["POST"])

        # Include the APIRouter in the FastAPI application.
        self.rp_app.include_router(api_router)

    def start_uvicorn(self, api_host='localhost', api_port=8000, api_concurrency=1):
        '''
        Starts the Uvicorn server.
        '''
        uvicorn.run(
            self.rp_app, host=api_host,
            port=int(api_port), workers=int(api_concurrency),
            log_level=os.environ.get("UVICORN_LOG_LEVEL", "info"),
            access_log=False
        )

    async def _run(self, job: Job):
        '''
        Performs model inference on the input data using the provided handler.
        If handler is not provided, returns an error message.
        '''
        if self.config["handler"] is None:
            return {"error": "Handler not provided"}

        # Set the current job ID.
        job_list.add_job(job.id)

        # Process the job using the provided handler.
        job_results = await run_job(self.config["handler"], job.__dict__)

        # Reset the job ID.
        job_list.remove_job(job.id)

        # Return the results of the job processing.
        return jsonable_encoder(job_results)

    async def _debug_run(self, job: TestJob):
        '''
        Performs model inference on the input data using the provided handler.
        '''
        if self.config["handler"] is None:
            return {"error": "Handler not provided"}

        # Set the current job ID.
        job_list.add_job(job.id)

        if is_generator(self.config["handler"]):
            generator_output = run_job_generator(self.config["handler"], job.__dict__)
            job_results = {"output": []}
            async for stream_output in generator_output:
                job_results["output"].append(stream_output["output"])
        else:
            job_results = await run_job(self.config["handler"], job.__dict__)

        job_results["id"] = job.id

        # Reset the job ID.
        job_list.remove_job(job.id)

        return jsonable_encoder(job_results)
