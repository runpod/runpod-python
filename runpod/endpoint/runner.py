'''
RunPod | Python | Endpoint Runner
'''

import time
import requests
from requests.adapters import HTTPAdapter, Retry


# ---------------------------------------------------------------------------- #
#                                    Client                                    #
# ---------------------------------------------------------------------------- #
class RunPodClient:
    ''' A client for running endpoint calls. '''

    def __init__(self):
        '''
        Initialize the client.
        '''
        from runpod import api_key, endpoint_url_base  # pylint: disable=import-outside-toplevel, cyclic-import
        if api_key is None:
            raise RuntimeError(
                "Expected `run_pod.api_key` to be initialized. "
                "You can solve this by running `run_pod.api_key = 'your-key'. "
                "An API key can be generated at "
                "https://www.runpod.io/console/user/settings"
            )
        self.rp_session = requests.Session()
        retries = Retry(total=5, backoff_factor=0.1, status_forcelist=[429])
        self.rp_session.mount('http://', HTTPAdapter(max_retries=retries))

        self.endpoint_url_base = endpoint_url_base
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

    def post(self, endpoint, data, timeout=10):
        '''
        Post to the endpoint.
        '''
        url = f"{self.endpoint_url_base}/{endpoint}"
        return self.rp_session.post(url, headers=self.headers, json=data, timeout=timeout)

    def get(self, endpoint, timeout=10):
        '''
        Get from the endpoint.
        '''
        url = f"{self.endpoint_url_base}/{endpoint}"
        return self.rp_session.get(url, headers=self.headers, timeout=timeout)


# ---------------------------------------------------------------------------- #
#                                   Endpoint                                   #
# ---------------------------------------------------------------------------- #
class Endpoint:
    ''' Creates a class to run an endpoint. '''

    def __init__(self, endpoint_id):
        '''
        Initializes the class.
        '''
        self.endpoint_id = endpoint_id
        self.rp_client = RunPodClient()

        print(f"Initialized endpoint: {self.endpoint_id}")

    def run(self, endpoint_input):
        '''
        Runs the endpoint.
        '''
        job_request = self.rp_client.post(
            endpoint=f"{self.endpoint_id}/run",
            data={"input": endpoint_input},
            timeout=10
        )

        if job_request.status_code == 401:
            raise RuntimeError("401 Unauthorized | Make sure Runpod API key is set and valid.")

        print(f"Started job: {job_request.json()['id']}")

        return Job(self.endpoint_id, job_request.json()["id"])

    def run_sync(self, endpoint_input):
        '''
        Blocking run where the job results are returned with the call.
        '''
        job_return = self.rp_client.post(
            endpoint=f"{self.endpoint_id}/runsync",
            data={"input": endpoint_input},
            timeout=60
        )

        return job_return.json()


# ---------------------------------------------------------------------------- #
#                                      Job                                     #
# ---------------------------------------------------------------------------- #
class Job:
    ''' Creates a class to run a job. '''

    def __init__(self, endpoint_id, job_id):
        ''' Initializes the class. '''

        self.endpoint_id = endpoint_id
        self.job_id = job_id
        self.rp_client = RunPodClient()

        self.job_output = None

    def _status_json(self):
        """
        Returns the raw json of the status, raises an exception if invalid
        """

        status_url = f"{self.endpoint_id}/status/{self.job_id}"

        status_request = self.rp_client.get(endpoint=status_url, timeout=10)
        request_json = status_request.json()

        if "error" in request_json:
            raise RuntimeError(f"Error from RunPod Server: '{request_json['error']}'")

        if "status" not in request_json:
            raise ValueError(f"Unexpected response from server: {request_json}")

        return request_json

    def status(self):
        '''
        Returns the status of the job request.
        '''
        return self._status_json()["status"]

    def output(self, timeout=60):
        '''
        Gets the output of the endpoint run request.
        '''
        while self.status() not in ["COMPLETED", "FAILED", "TIMEOUT"]:
            time.sleep(.1)
            timeout -= .1

        if self.job_output is None:
            status_json = self._status_json()
            if "output" not in status_json:
                return None
            self.job_output = status_json["output"]

        return self.job_output
