''' Used to launch the FastAPI web server when worker is running in API mode. '''
# pylint: disable=too-few-public-methods

import os

import uvicorn
from fastapi import FastAPI, APIRouter
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from .rp_job import run_job
from .worker_state import Jobs
from .rp_ping import HeartbeatSender

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

heartbeat = HeartbeatSender()


# ------------------------------- Input Objects ------------------------------ #
class Job(BaseModel):
    ''' Represents a job. '''
    id: str
    input: dict | list | str | int | float | bool


class TestJob(BaseModel):
    ''' Represents a test job.
    input can be any type of data.
    '''
    id: str = "test_job"
    input: dict | list | str | int | float | bool


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

        import runpod  # pylint: disable=import-outside-toplevel,cyclic-import

        self.rp_app = FastAPI(
            title="RunPod | Test Worker | API",
            description=DESCRIPTION,
            version=runpod.__version__,
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
            log_level="info",
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

        job_results = await run_job(self.config["handler"], job.__dict__)

        job_results["id"] = job.id

        # Reset the job ID.
        job_list.remove_job(job.id)

        return jsonable_encoder(job_results)
