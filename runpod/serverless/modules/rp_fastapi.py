''' Used to launch the FastAPI web server when worker is running in API mode. '''

import os

import uvicorn
from fastapi import FastAPI, APIRouter
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from .job import run_job
from .worker_state import set_job_id
from .heartbeat import start_ping


class Job(BaseModel):
    ''' Represents a job. '''
    id: str
    input: dict


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
        start_ping()

        # Set the handler for processing jobs.
        self.config = {"handler": handler}

        # Initialize the FastAPI web server.
        self.rp_app = FastAPI()

        # Create an APIRouter and add the route for processing jobs.
        api_router = APIRouter()
        api_router.add_api_route(
            f"/{os.environ.get('RUNPOD_ENDPOINT_ID')}/realtime",
            self.run, methods=["POST"]
        )

        # Include the APIRouter in the FastAPI application.
        self.rp_app.include_router(api_router)

    def start_uvicorn(self, api_port, api_concurrency):
        '''
        Starts the Uvicorn server.
        '''
        uvicorn.run(
            self.rp_app, host='0.0.0.0',
            port=int(api_port), workers=int(api_concurrency)
        )

    async def run(self, job: Job):
        '''
        Performs model inference on the input data using the provided handler.
        If handler is not provided, returns an error message.
        '''
        if self.config["handler"] is None:
            return {"error": "Handler not provided"}

        # Set the current job ID.
        set_job_id(job.id)

        # Process the job using the provided handler.
        job_results = run_job(self.config["handler"], job.__dict__)

        # Reset the job ID.
        set_job_id(None)

        # Return the results of the job processing.
        return jsonable_encoder(job_results)
