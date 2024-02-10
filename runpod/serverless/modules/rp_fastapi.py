''' Used to launch the FastAPI web server when worker is running in API mode. '''
# pylint: disable=too-few-public-methods

import os
import uuid
import asyncio
from dataclasses import dataclass
from typing import Union, Optional, Dict, Any

import aiohttp
import uvicorn
from fastapi import FastAPI, APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import RedirectResponse

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
@dataclass
class Job:
    ''' Represents a job. '''
    id: str
    input: Union[dict, list, str, int, float, bool]


@dataclass
class TestJob:
    ''' Represents a test job.
    input can be any type of data.
    '''
    id: Optional[str] = None
    input: Optional[Union[dict, list, str, int, float, bool]] = None
    webhook: Optional[str] = None


@dataclass
class DefaultRequest:
    """ Represents a test input. """
    input: Dict[str, Any]
    webhook: Optional[str] = None


# ------------------------------ Output Objects ------------------------------ #
@dataclass
class JobOutput:
    ''' Represents the output of a job. '''
    id: str
    status: str
    output: Optional[Union[dict, list, str, int, float, bool]] = None
    error: Optional[str] = None


@dataclass
class StreamOutput:
    """ Stream representation of a job. """
    id: str
    status: str = "IN_PROGRESS"
    stream: Optional[Union[dict, list, str, int, float, bool]] = None
    error: Optional[str] = None


# ------------------------------ Webhook Sender ------------------------------ #
async def _send_webhook_async(url: str, payload: Dict[str, Any]) -> None:
    """
    Sends a webhook to the provided URL asynchronously. Retries once if the first attempt fails.

    Args:
        url (str): The URL to send the webhook to.
        payload (Dict[str, Any]): The JSON payload to send.
    """
    async def attempt_send(session, url, payload):
        try:
            async with session.post(url, json=payload, timeout=10) as response:
                response.raise_for_status()  # Raises exception for 4xx/5xx responses
                return True
        except (aiohttp.ClientError, aiohttp.http_exceptions.HttpProcessingError) as err:
            print(f"Request to {url} failed: {err}")
            return False

    async with aiohttp.ClientSession() as session:
        success = await attempt_send(session, url, payload)
        if not success:
            print("Retrying...")
            await asyncio.sleep(1)  # Wait for 1 second before retrying
            success = await attempt_send(session, url, payload)

        print("Failed to send webhook after retry.")
        return False


# ---------------------------------------------------------------------------- #
#                                  API Worker                                  #
# ---------------------------------------------------------------------------- #
class WorkerAPI:
    ''' Used to launch the FastAPI web server when the worker is running in API mode. '''

    def __init__(self, config: Dict[str, Any]):
        '''
        Initializes the WorkerAPI class.
        1. Starts the heartbeat thread.
        2. Initializes the FastAPI web server.
        3. Sets the handler for processing jobs.
        '''
        # Start the heartbeat thread.
        heartbeat.start_ping()

        self.config = config

        # Initialize the FastAPI web server.
        self.rp_app = FastAPI(
            title="RunPod | Test Worker | API",
            description=DESCRIPTION,
            version=runpod_version,
            docs_url="/"
        )

        # Create an APIRouter and add the route for processing jobs.
        api_router = APIRouter()

        # Docs Redirect /docs -> /
        api_router.add_api_route(
            "/docs", lambda: RedirectResponse(url="/"),
            include_in_schema=False
        )

        if RUNPOD_ENDPOINT_ID:
            api_router.add_api_route(f"/{RUNPOD_ENDPOINT_ID}/realtime",
                                     self._realtime, methods=["POST"])

        # Simulation endpoints.
        api_router.add_api_route(
            "/run", self._sim_run, methods=["POST"], response_model_exclude_none=True,
            summary="Simulate run behavior.",
            description="Returns job ID to be used with `/stream` and `/status` endpoints."
        )
        api_router.add_api_route(
            "/runsync", self._sim_runsync, methods=["POST"], response_model_exclude_none=True,
            summary="Simulate runsync behavior.",
            description="Returns job output directly when called."
        )
        api_router.add_api_route(
            "/stream/{job_id}", self._sim_stream, methods=["POST", "GET"],
            response_model_exclude_none=True, summary="Simulate stream behavior.",
            description="Aggregates the output of the job and returns it when the job is complete."
        )
        api_router.add_api_route(
            "/status/{job_id}", self._sim_status, methods=["POST"],
            response_model_exclude_none=True, summary="Simulate status behavior.",
            description="Returns the output of the job when the job is complete."
        )

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

    # ----------------------------- Realtime Endpoint ---------------------------- #
    async def _realtime(self, job: Job):
        '''
        Performs model inference on the input data using the provided handler.
        If handler is not provided, returns an error message.
        '''
        job_list.add_job(job.id)

        # Process the job using the provided handler, passing in the job input.
        job_results = await run_job(self.config["handler"], job.__dict__)

        job_list.remove_job(job.id)

        # Return the results of the job processing.
        return jsonable_encoder(job_results)

    # ---------------------------------------------------------------------------- #
    #                             Simulation Endpoints                             #
    # ---------------------------------------------------------------------------- #

    # ------------------------------------ run ----------------------------------- #
    async def _sim_run(self, job_request: DefaultRequest) -> JobOutput:
        """ Development endpoint to simulate run behavior. """
        assigned_job_id = f"test-{uuid.uuid4()}"
        job_list.add_job(assigned_job_id, job_request.input, job_request.webhook)
        return jsonable_encoder({"id": assigned_job_id, "status": "IN_PROGRESS"})

    # ---------------------------------- runsync --------------------------------- #
    async def _sim_runsync(self, job_request: DefaultRequest) -> JobOutput:
        """ Development endpoint to simulate runsync behavior. """
        assigned_job_id = f"test-{uuid.uuid4()}"
        job = TestJob(id=assigned_job_id, input=job_request.input)

        if is_generator(self.config["handler"]):
            generator_output = run_job_generator(self.config["handler"], job.__dict__)
            job_output = {"output": []}
            async for stream_output in generator_output:
                job_output['output'].append(stream_output["output"])
        else:
            job_output = await run_job(self.config["handler"], job.__dict__)

        if job_output.get('error', None):
            return jsonable_encoder({
                "id": job.id,
                "status": "FAILED",
                "error": job_output['error']
            })

        if job_request.webhook:
            await _send_webhook_async(job_request.webhook, job_output)

        return jsonable_encoder({
            "id": job.id,
            "status": "COMPLETED",
            "output": job_output['output']
        })

    # ---------------------------------- stream ---------------------------------- #
    async def _sim_stream(self, job_id: str) -> StreamOutput:
        """ Development endpoint to simulate stream behavior. """
        stashed_job = job_list.get_job(job_id)
        if stashed_job is None:
            return jsonable_encoder({
                "id": job_id,
                "status": "FAILED",
                "error": "Job ID not found"
            })

        job = TestJob(id=job_id, input=stashed_job.input)

        if is_generator(self.config["handler"]):
            generator_output = run_job_generator(self.config["handler"], job.__dict__)
            stream_accumulator = []
            async for stream_output in generator_output:
                stream_accumulator.append({"output": stream_output["output"]})
        else:
            return jsonable_encoder({
                "id": job_id,
                "status": "FAILED",
                "error": "Stream not supported, handler must be a generator."
            })

        job_list.remove_job(job.id)

        if stashed_job.webhook:
            await _send_webhook_async(stashed_job.webhook, stream_accumulator)

        return jsonable_encoder({
            "id": job_id,
            "status": "COMPLETED",
            "stream": stream_accumulator
        })

    # ---------------------------------- status ---------------------------------- #
    async def _sim_status(self, job_id: str) -> JobOutput:
        """ Development endpoint to simulate status behavior. """
        stashed_job = job_list.get_job(job_id)
        if stashed_job is None:
            return jsonable_encoder({
                "id": job_id,
                "status": "FAILED",
                "error": "Job ID not found"
            })

        job = TestJob(id=stashed_job.id, input=stashed_job.input)

        if is_generator(self.config["handler"]):
            generator_output = run_job_generator(self.config["handler"], job.__dict__)
            job_output = {"output": []}
            async for stream_output in generator_output:
                job_output['output'].append(stream_output["output"])
        else:
            job_output = await run_job(self.config["handler"], job.__dict__)

        job_list.remove_job(job.id)

        if job_output.get('error', None):
            return jsonable_encoder({
                "id": job_id,
                "status": "FAILED",
                "error": job_output['error']
            })

        if stashed_job.webhook:
            await _send_webhook_async(stashed_job.webhook, job_output)

        return jsonable_encoder({
            "id": job_id,
            "status": "COMPLETED",
            "output": job_output['output']
        })
