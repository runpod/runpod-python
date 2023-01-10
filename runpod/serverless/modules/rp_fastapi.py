''' Used to launch the FastAPI web server when worker is running in API mode. '''

import json

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from .job import run_job
from .worker_state import set_job_id


class Job(BaseModel):
    ''' Represents a job. '''
    id: str
    input: dict


class WorkerAPI:
    ''' Used to launch the FastAPI web server when worker is running in API mode. '''

    def __init__(self):
        '''
        Initializes the WorkerAPI class.
        '''
        self.config = {"handler": None}
        self.rp_app = FastAPI()
        self.rp_app.add_api_route("/run", self.run, methods=["POST"])

    def start_uvicorn(self, api_port):
        ''' Starts the Uvicorn server. '''
        uvicorn.run(self.rp_app, port=int(api_port))

    async def run(self, job: Job):
        '''
        Performs model inference on the input data.
        '''
        set_job_id(job.id)

        job_results = run_job(self.config["handler"], job.__dict__)

        job_data = json.dumps(job_results, ensure_ascii=False)

        set_job_id(None)

        return job_data
