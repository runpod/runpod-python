""" Used to launch the FastAPI web server when worker is running in API mode. """

import os
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Union

import requests
import uvicorn
from fastapi import APIRouter, FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.responses import RedirectResponse

from ...http_client import SyncClientSession
from ...version import __version__ as runpod_version
from .rp_handler import is_generator
from .rp_job import run_job, run_job_generator
from .rp_ping import Heartbeat
from .worker_state import Job, JobsProgress

RUNPOD_ENDPOINT_ID = os.environ.get("RUNPOD_ENDPOINT_ID", None)

TITLE = "Runpod | Development Worker API"

DESCRIPTION = """
The Development Worker API facilitates testing and debugging of your Runpod workers.
It offers a sandbox environment for executing code and simulating interactions with your worker, ensuring your applications can seamlessly transition to production on Runpod serverless platform.
Use this API for comprehensive testing of request submissions and result retrieval, mimicking the behavior of Runpod's operational environment.
---
*Note: This API serves as a local testing tool and will not be utilized once your worker is operational on the Runpod platform.*
"""

# Add CLI tool suggestion if RUNPOD_PROJECT_ID is not set.
if os.environ.get("RUNPOD_PROJECT_ID", None) is None:
    DESCRIPTION += """

    ℹ️ | Consider developing with our CLI tool to streamline your worker development process.

    >_  wget -qO- cli.runpod.net | sudo bash
    >_  runpodctl project create
    """

RUN_DESCRIPTION = """
Initiates processing jobs, returning a unique job ID.

**Parameters:**
- **input** (string): The data to be processed by the worker. This could be a string, JSON object, etc., depending on the worker's requirements.
- **webhook** (string, optional): A callback URL for result notification upon completion. If specified, the server will send a POST request to this URL with the job's result once it's available.

**Returns:**
- **job_id** (string): A unique identifier for the job, used with the `/stream` and `/status` endpoints for monitoring progress and checking job status.
"""

RUNSYNC_DESCRIPTION = """
Executes processing jobs synchronously, returning the job's output directly.

This endpoint is ideal for tasks where immediate result retrieval is necessary,
streamlining the execution process by eliminating the need for subsequent
status or result checks.

**Parameters:**
- **input** (string): The data to be processed by the worker. This should be in a format that the worker can understand (e.g., JSON, text, etc.).
- **webhook** (string, optional): A callback URL to which the result will be posted. While direct result retrieval is the primary operation mode for this endpoint, specifying a webhook allows for asynchronous result notification if needed.

**Returns:**
- **output** (Any): The direct output from the processing job, formatted according to the job's nature and the expected response structure. This could be a JSON object, plain text, or any data structure depending on the processing logic.
"""

STREAM_DESCRIPTION = """
Continuously aggregates the output of a processing job, returning the full output once the job is complete.

This endpoint is especially useful for jobs where the complete output needs to be accessed at once. It provides a consolidated view of the results post-completion, ensuring that users can retrieve the entire output without the need to poll multiple times or manage partial results.

**Parameters:**
- **job_id** (string): The unique identifier of the job for which output is being requested. This ID is used to track the job's progress and aggregate its output.

**Returns:**
- **output** (Any): The aggregated output from the job, returned as a single entity once the job has concluded. The format of the output will depend on the nature of the job and how its results are structured.
"""

STATUS_DESCRIPTION = """
Checks the completion status of a processing job and returns its output if the job is complete.

This endpoint is invaluable for monitoring the progress of a job and obtaining the output only after the job has fully completed. It simplifies the process of querying job completion and retrieving results, eliminating the need for continuous polling or result aggregation.

**Parameters:**
- **job_id** (string): The unique identifier for the job being queried. This ID is used to track and assess the status of the job.

**Returns:**
- **status** (string): The completion status of the job, typically 'complete' or 'in progress'. This status indicates whether the job has finished processing and if the output is ready for retrieval.
- **output** (Any, optional): The final output of the job, provided if the job is complete. The format and structure of the output depend on the job's nature and the data processing involved.

**Note:** The availability of the `output` field is contingent on the job's completion status. If the job is still in progress, this field may be omitted or contain partial results, depending on the implementation.
"""


# ------------------------------ Initializations ----------------------------- #
job_list = JobsProgress()
heartbeat = Heartbeat()


# ------------------------------- Input Objects ------------------------------ #
@dataclass
class Job:
    """Represents a job."""

    id: str
    input: Union[dict, list, str, int, float, bool]


@dataclass
class TestJob:
    """Represents a test job.
    input can be any type of data.
    """

    id: Optional[str] = None
    input: Optional[Union[dict, list, str, int, float, bool]] = None
    webhook: Optional[str] = None


@dataclass
class DefaultRequest:
    """Represents a test input."""

    input: Dict[str, Any]
    webhook: Optional[str] = None


# ------------------------------ Output Objects ------------------------------ #
@dataclass
class JobOutput:
    """Represents the output of a job."""

    id: str
    status: str
    output: Optional[Union[dict, list, str, int, float, bool]] = None
    error: Optional[str] = None


@dataclass
class StreamOutput:
    """Stream representation of a job."""

    id: str
    status: str = "IN_PROGRESS"
    stream: Optional[Union[dict, list, str, int, float, bool]] = None
    error: Optional[str] = None


# ------------------------------ Webhook Sender ------------------------------ #
def _send_webhook(url: str, payload: Dict[str, Any]) -> bool:
    """
    Sends a webhook to the provided URL.

    Args:
        url (str): The URL to send the webhook to.
        payload (Dict[str, Any]): The JSON payload to send.

    Returns:
        bool: True if the request was successful, False otherwise.
    """
    with SyncClientSession() as session:
        try:
            response = session.post(url, json=payload, timeout=10)
            response.raise_for_status()  # Raises exception for 4xx/5xx responses
            return True
        except requests.RequestException as err:
            print(f"WEBHOOK | Request to {url} failed: {err}")
            return False


# ---------------------------------------------------------------------------- #
#                                  API Worker                                  #
# ---------------------------------------------------------------------------- #
class WorkerAPI:
    """Used to launch the FastAPI web server when the worker is running in API mode."""

    def __init__(self, config: Dict[str, Any]):
        """
        Initializes the WorkerAPI class.
        1. Starts the heartbeat thread.
        2. Initializes the FastAPI web server.
        3. Sets the handler for processing jobs.
        """
        # Start the heartbeat thread.
        heartbeat.start_ping()

        self.config = config

        tags_metadata = [
            {
                "name": "Synchronously Submit Request & Get Job Results",
                "description": "Endpoints for submitting job requests and getting the results.",
            },
            {
                "name": "Submit Job Requests",
                "description": "Endpoints for submitting job requests.",
            },
            {
                "name": "Check Job Results",
                "description": "Endpoints for checking the status of a job and getting the results.",
            },
        ]

        # Initialize the FastAPI web server.
        self.rp_app = FastAPI(
            title=TITLE,
            description=DESCRIPTION,
            version=runpod_version,
            docs_url="/",
            openapi_tags=tags_metadata,
        )

        # Create an APIRouter and add the route for processing jobs.
        api_router = APIRouter()

        # Docs Redirect /docs -> /
        api_router.add_api_route(
            "/docs", lambda: RedirectResponse(url="/"), include_in_schema=False
        )

        if RUNPOD_ENDPOINT_ID:
            api_router.add_api_route(
                f"/{RUNPOD_ENDPOINT_ID}/realtime", self._realtime, methods=["POST"]
            )

        # Simulation endpoints.
        api_router.add_api_route(
            "/run",
            self._sim_run,
            methods=["POST"],
            response_model_exclude_none=True,
            summary="Mimics the behavior of the run endpoint.",
            description=RUN_DESCRIPTION,
            tags=["Submit Job Requests"],
        )
        api_router.add_api_route(
            "/runsync",
            self._sim_runsync,
            methods=["POST"],
            response_model_exclude_none=True,
            summary="Mimics the behavior of the runsync endpoint.",
            description=RUNSYNC_DESCRIPTION,
            tags=["Synchronously Submit Request & Get Job Results"],
        )
        api_router.add_api_route(
            "/stream/{job_id}",
            self._sim_stream,
            methods=["POST"],
            response_model_exclude_none=True,
            summary="Mimics the behavior of the stream endpoint.",
            description=STREAM_DESCRIPTION,
            tags=["Check Job Results"],
        )
        api_router.add_api_route(
            "/status/{job_id}",
            self._sim_status,
            methods=["POST"],
            response_model_exclude_none=True,
            summary="Mimics the behavior of the status endpoint.",
            description=STATUS_DESCRIPTION,
            tags=["Check Job Results"],
        )

        # Include the APIRouter in the FastAPI application.
        self.rp_app.include_router(api_router)

    def start_uvicorn(self, api_host="localhost", api_port=8000, api_concurrency=1):
        """
        Starts the Uvicorn server.
        """
        uvicorn.run(
            self.rp_app,
            host=api_host,
            port=int(api_port),
            workers=int(api_concurrency),
            log_level=os.environ.get("UVICORN_LOG_LEVEL", "info"),
            access_log=False,
        )

    # ----------------------------- Realtime Endpoint ---------------------------- #
    async def _realtime(self, job: Job):
        """
        Performs model inference on the input data using the provided handler.
        If handler is not provided, returns an error message.
        """
        job_list.add(job.id)

        # Process the job using the provided handler, passing in the job input.
        job_results = await run_job(self.config["handler"], job.__dict__)

        job_list.remove(job.id)

        # Return the results of the job processing.
        return jsonable_encoder(job_results)

    # ---------------------------------------------------------------------------- #
    #                             Simulation Endpoints                             #
    # ---------------------------------------------------------------------------- #

    # ------------------------------------ run ----------------------------------- #
    async def _sim_run(self, job_request: DefaultRequest) -> JobOutput:
        """Development endpoint to simulate run behavior."""
        assigned_job_id = f"test-{uuid.uuid4()}"
        job_list.add({
            "id": assigned_job_id,
            "input": job_request.input,
            "webhook": job_request.webhook
        })
        return jsonable_encoder({"id": assigned_job_id, "status": "IN_PROGRESS"})

    # ---------------------------------- runsync --------------------------------- #
    async def _sim_runsync(self, job_request: DefaultRequest) -> JobOutput:
        """Development endpoint to simulate runsync behavior."""
        assigned_job_id = f"test-{uuid.uuid4()}"
        job = TestJob(id=assigned_job_id, input=job_request.input)

        if is_generator(self.config["handler"]):
            generator_output = run_job_generator(self.config["handler"], job.__dict__)
            job_output = {"output": []}
            async for stream_output in generator_output:
                job_output["output"].append(stream_output["output"])
        else:
            job_output = await run_job(self.config["handler"], job.__dict__)

        if job_output.get("error", None):
            return jsonable_encoder(
                {"id": job.id, "status": "FAILED", "error": job_output["error"]}
            )

        if job_request.webhook:
            thread = threading.Thread(
                target=_send_webhook,
                args=(job_request.webhook, job_output),
                daemon=True,
            )
            thread.start()

        return jsonable_encoder(
            {"id": job.id, "status": "COMPLETED", "output": job_output["output"]}
        )

    # ---------------------------------- stream ---------------------------------- #
    async def _sim_stream(self, job_id: str) -> StreamOutput:
        """Development endpoint to simulate stream behavior."""
        stashed_job = job_list.get(job_id)
        if stashed_job is None:
            return jsonable_encoder(
                {"id": job_id, "status": "FAILED", "error": "Job ID not found"}
            )

        job = TestJob(id=job_id, input=stashed_job.input)

        if is_generator(self.config["handler"]):
            generator_output = run_job_generator(self.config["handler"], job.__dict__)
            stream_accumulator = []
            async for stream_output in generator_output:
                stream_accumulator.append({"output": stream_output["output"]})
        else:
            return jsonable_encoder(
                {
                    "id": job_id,
                    "status": "FAILED",
                    "error": "Stream not supported, handler must be a generator.",
                }
            )

        job_list.remove(job.id)

        if stashed_job.webhook:
            thread = threading.Thread(
                target=_send_webhook,
                args=(stashed_job.webhook, stream_accumulator),
                daemon=True,
            )
            thread.start()

        return jsonable_encoder(
            {"id": job_id, "status": "COMPLETED", "stream": stream_accumulator}
        )

    # ---------------------------------- status ---------------------------------- #
    async def _sim_status(self, job_id: str) -> JobOutput:
        """Development endpoint to simulate status behavior."""
        stashed_job = job_list.get(job_id)
        if stashed_job is None:
            return jsonable_encoder(
                {"id": job_id, "status": "FAILED", "error": "Job ID not found"}
            )

        job = TestJob(id=stashed_job.id, input=stashed_job.input)

        if is_generator(self.config["handler"]):
            generator_output = run_job_generator(self.config["handler"], job.__dict__)
            job_output = {"output": []}
            async for stream_output in generator_output:
                job_output["output"].append(stream_output["output"])
        else:
            job_output = await run_job(self.config["handler"], job.__dict__)

        job_list.remove(job.id)

        if job_output.get("error", None):
            return jsonable_encoder(
                {"id": job_id, "status": "FAILED", "error": job_output["error"]}
            )

        if stashed_job.webhook:
            thread = threading.Thread(
                target=_send_webhook,
                args=(stashed_job.webhook, job_output),
                daemon=True,
            )
            thread.start()

        return jsonable_encoder(
            {"id": job_id, "status": "COMPLETED", "output": job_output["output"]}
        )
