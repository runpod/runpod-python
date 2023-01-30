''' Used to launch the FastAPI web server when worker is running in API mode. '''

import os
import threading

import uvicorn
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from .job import run_job
from .worker_state import set_job_id
from .heartbeat import start_heartbeat


class Job(BaseModel):
    ''' Represents a job. '''
    id: str
    input: dict


class WorkerAPI:
    ''' Used to launch the FastAPI web server when worker is running in API mode. '''

    def __init__(self):
        '''
        Initializes the WorkerAPI class.
        1. Starts the heartbeat thread.
        2. Initializes the FastAPI web server.
        '''
        heartbeat_thread = threading.Thread(target=start_heartbeat)
        heartbeat_thread.daemon = True
        heartbeat_thread.start()

        self.config = {"handler": None}
        self.rp_app = FastAPI()
        self.rp_app.add_api_route(f"/{os.environ.get('RUNPOD_ENDPOINT_ID')}/realtime",
                                  self.run, methods=["POST"])

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
        Performs model inference on the input data.
        '''
        set_job_id(job.id)

        job_results = run_job(self.config["handler"], job.__dict__)

        set_job_id(None)

        return jsonable_encoder(job_results)
