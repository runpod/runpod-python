'''
RunPod | Python | Endpoint Runner
'''

import time
import requests


class Endpoint:
    ''' Creates a class to run an endpoint. '''

    def __init__(self, endpoint_id):
        ''' Initializes the class. '''

        from runpod import api_key, endpoint_url_base  # pylint: disable=import-outside-toplevel

        self.endpoint_id = endpoint_id

        self.endpoint_url = f"{endpoint_url_base}/{self.endpoint_id}/run"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

        print(f"endpoint_url: {self.endpoint_url}")
        print(f"headers: {self.headers}")

    def run(self, endpoint_input):
        '''
        Runs the endpoint.
        '''
        job_request = requests.post(
            self.endpoint_url, headers=self.headers,
            json={"input": endpoint_input}, timeout=10
        )

        print(f"return text: {job_request.text}")

        return Job(self.endpoint_id, job_request.json()["id"])

    def run_sync(self, endpoint_input):
        '''
        Blocking run where the job results are returned with the call.
        '''
        job_return = requests.post(
            self.endpoint_url, headers=self.headers,
            json={"input": endpoint_input}, timeout=100
        )

        return job_return.json()


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
        from runpod import api_key, endpoint_url_base  # pylint: disable=import-outside-toplevel,cyclic-import

        status_url = f"{endpoint_url_base}/{self.endpoint_id}/status/{self.job_id}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

        status_request = requests.get(status_url, headers=headers, timeout=10)

        return status_request.json()["status"]

    def output(self):
        '''
        Gets the output of the endpoint run request.
        If blocking is True, the method will block until the endpoint run is complete.
        '''
        from runpod import api_key, endpoint_url_base  # pylint: disable=import-outside-toplevel,cyclic-import

        while self.status() not in ["COMPLETED", "FAILED"]:
            time.sleep(.1)

        output_url = f"{endpoint_url_base}/{self.endpoint_id}/status/{self.job_id}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

        output_request = requests.get(output_url, headers=headers, timeout=10)

        return output_request.json()["output"]
