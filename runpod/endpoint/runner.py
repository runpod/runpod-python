'''
RunPod | Python | Endpoint Runner
'''
# pylint: disable=too-few-public-methods

import time
import requests

import runpod


class Endpoint:
    ''' Creates a class to run an endpoint. '''

    def __init__(self, endpoint_id):
        ''' Initializes the class. '''

        self.endpoint_id = endpoint_id

    def run(self, endpoint_input):
        '''
        Runs the endpoint.
        '''
        endpoint_url = f"{runpod.endpoint_url_base}/{self.endpoint_id}/run"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {runpod.api_key}"
        }

        job_input = {"input": endpoint_input}

        job_request = requests.post(endpoint_url, headers=headers, json=job_input, timeout=10)

        return Job(self.endpoint_id, job_request.json()["id"])


class Job:
    ''' Creates a class to run a job. '''

    def __init__(self, endpoint_id, job_id):
        ''' Initializes the class. '''

        self.endpoint_id = endpoint_id
        self.job_id = job_id

    def status(self):
        '''
        Returns the status of the job request.
        '''
        status_url = f"{runpod.endpoint_url_base}/{self.endpoint_id}/status/{self.job_id}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {runpod.api_key}"
        }

        status_request = requests.get(status_url, headers=headers, timeout=10)

        return status_request.json()["status"]

    def output(self):
        '''
        Gets the output of the endpoint run request.
        If blocking is True, the method will block until the endpoint run is complete.
        '''
        while self.status() not in ["COMPLETED", "FAILED"]:
            time.sleep(.1)

        output_url = f"{runpod.endpoint_url_base}/{self.endpoint_id}/status/{self.job_id}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {runpod.api_key}"
        }

        output_request = requests.get(output_url, headers=headers, timeout=10)

        return output_request.json()["output"]
