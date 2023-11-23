'''
RunPod | Python | Endpoint Runner
'''
from typing import Any, Optional, Dict
import time
import requests
from requests.adapters import HTTPAdapter, Retry

# Exception Messages
UNAUTHORIZED_MSG = "401 Unauthorized | Make sure Runpod API key is set and valid."
API_KEY_NOT_SET_MSG = ("Expected `run_pod.api_key` to be initialized. "
                       "You can solve this by setting `run_pod.api_key = 'your-key'. "
                       "An API key can be generated at "
                       "https://runpod.io/console/user/settings")

def is_completed(status:str)->bool:
    """Returns true if status is one of the possible final states for a serverless request."""
    return status in ["COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"]
# ---------------------------------------------------------------------------- #
#                                    Client                                    #
# ---------------------------------------------------------------------------- #
class RunPodClient:
    """A client for running endpoint calls."""

    def __init__(self):
        """
        Initialize a RunPodClient instance.

        Raises:
            RuntimeError: If the API key has not been initialized.
        """
        from runpod import api_key, endpoint_url_base  # pylint: disable=import-outside-toplevel, cyclic-import

        if api_key is None:
            raise RuntimeError(API_KEY_NOT_SET_MSG)

        self.rp_session = requests.Session()
        retries = Retry(total=5, backoff_factor=1, status_forcelist=[429])
        self.rp_session.mount('http://', HTTPAdapter(max_retries=retries))

        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

        self.endpoint_url_base = endpoint_url_base

    def _request(self,
                 method: str, endpoint: str, data: Optional[dict] = None, timeout: int = 10):
        """
        Make a request to the specified endpoint using the given HTTP method.

        Args:
            method: The HTTP method to use ('GET' or 'POST').
            endpoint: The endpoint path to which the request will be made.
            data: The JSON payload to send with the request.
            timeout: The number of seconds to wait for the server to send data before giving up.

        Returns:
            The JSON response from the server.

        Raises:
            RuntimeError: If the response returns a 401 Unauthorized status.
            requests.HTTPError: If the response contains an unsuccessful status code.
        """
        url = f"{self.endpoint_url_base}/{endpoint}"
        response = self.rp_session.request(
            method, url, headers=self.headers, json=data, timeout=timeout)

        if response.status_code == 401:
            raise RuntimeError(UNAUTHORIZED_MSG)

        response.raise_for_status()
        return response.json()

    def post(self, endpoint: str, data: dict, timeout: int = 10):
        """ Post to the endpoint. """
        return self._request('POST', endpoint, data, timeout)

    def get(self, endpoint: str, timeout: int = 10):
        """ Get from the endpoint. """
        return self._request('GET', endpoint, timeout=timeout)


# ---------------------------------------------------------------------------- #
#                                      Job                                     #
# ---------------------------------------------------------------------------- #
class Job:
    """Represents a job to be run on the RunPod service."""

    def __init__(self, endpoint_id: str, job_id: str, client: RunPodClient):
        """
        Initialize a Job instance with the given endpoint ID and job ID.

        Args:
            endpoint_id: The identifier for the endpoint.
            job_id: The identifier for the job.
            client: An instance of the RunPodClient to make requests with.
        """
        self.endpoint_id = endpoint_id
        self.job_id = job_id
        self.rp_client = client

        self.job_status = None
        self.job_output = None

    def _fetch_job(self):
        """ Returns the raw json of the status, raises an exception if invalid """
        status_url = f"{self.endpoint_id}/status/{self.job_id}"
        job_state = self.rp_client.get(endpoint=status_url)

        if is_completed(job_state["status"]):
            self.job_status = job_state["status"]
            self.job_output = job_state.get("output", None)

        return job_state

    def status(self):
        """ Returns the status of the job request. """
        if self.job_status is not None:
            return self.job_status

        return self._fetch_job()["status"]

    def output(self, timeout: int = 0) -> Any:
        """
        Returns the output of the job request.

        Args:
            timeout: The number of seconds to wait for the server to send data before giving up.
        """
        if timeout > 0:
            while not is_completed(self.status()):
                time.sleep(1)
                timeout -= 1
                if timeout <= 0:
                    raise TimeoutError("Job timed out.")

        if self.job_output is not None:
            return self.job_output

        return self._fetch_job().get("output", None)

    def cancel(self, timeout: int = 3) -> Any:
        """
        Cancels the job and returns the result of the cancellation request.

        Args:
            timeout: The number of seconds to wait for the server to respond before giving up.
        """
        return self.rp_client.post(f"{self.endpoint_id}/cancel/{self.job_id}",
                                   data=None,timeout=timeout)



# ---------------------------------------------------------------------------- #
#                                   Endpoint                                   #
# ---------------------------------------------------------------------------- #
class Endpoint:
    """Manages an endpoint to run jobs on the RunPod service."""

    def __init__(self, endpoint_id: str):
        """
        Initialize an Endpoint instance with the given endpoint ID.

        Args:
            endpoint_id: The identifier for the endpoint.

        Example:
            >>> endpoint = runpod.Endpoint("ENDPOINT_ID")
            >>> run_request = endpoint.run({"your_model_input_key": "your_model_input_value"})
            >>> print(run_request.status())
            >>> print(run_request.output())
        """
        self.endpoint_id = endpoint_id
        self.rp_client = RunPodClient()

    def run(self, request_input: Dict[str, Any]) -> Job:
        """
        Run the endpoint with the given input.

        Args:
            request_input: The input to pass into the endpoint.

        Returns:
            A Job instance for the run request.
        """
        if not request_input.get("input"):
            request_input = {"input": request_input}

        job_request = self.rp_client.post(f"{self.endpoint_id}/run", request_input)
        return Job(self.endpoint_id, job_request["id"], self.rp_client)

    def run_sync(self, request_input: Dict[str, Any], timeout: int = 86400) -> Dict[str, Any]:
        """
        Run the endpoint with the given input synchronously.

        Args:
            request_input: The input to pass into the endpoint.
        """
        if not request_input.get("input"):
            request_input = {"input": request_input}

        job_request = self.rp_client.post(
            f"{self.endpoint_id}/runsync", request_input, timeout=timeout)

        if job_request["status"] in ["COMPLETED", "FAILED", "TIMEOUT"]:
            return job_request.get("output", None)

        return Job(self.endpoint_id, job_request["id"], self.rp_client).output(timeout=timeout)

    def health(self,timeout: int = 3) -> Dict[str, Any]:
        """
        Check the health of the endpoint (number/state of workers, number/state of requests).

        Args:
            timeout: The number of seconds to wait for the server to respond before giving up.
        """
        return self.rp_client.get(f"{self.endpoint_id}/health",timeout=timeout)
    def purge_queue(self,timeout: int = 3) -> Dict[str, Any]:
        """
        Purges the endpoint's job queue and returns the result of the purge request.

        Args:
            timeout: The number of seconds to wait for the server to respond before giving up.
        """
        return self.rp_client.post(f"{self.endpoint_id}/purge-queue",data=None,timeout=timeout)
