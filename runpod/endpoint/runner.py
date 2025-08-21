"""
Runpod | Python | Endpoint Runner
"""

import time
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter, Retry

import runpod
from runpod.endpoint.helpers import (
    API_KEY_NOT_SET_MSG,
    FINAL_STATES,
    UNAUTHORIZED_MSG,
    is_completed,
)


# ---------------------------------------------------------------------------- #
#                                    Client                                    #
# ---------------------------------------------------------------------------- #
class RunPodClient:
    """A client for running endpoint calls."""

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize a RunPodClient instance.

        Args:
            api_key: Optional API key. If not provided, uses global api_key.

        Raises:
            RuntimeError: If the API key has not been initialized.
        """
        # Use provided api_key or fall back to global
        self.api_key = api_key or runpod.api_key
        
        if self.api_key is None:
            raise RuntimeError(API_KEY_NOT_SET_MSG)

        self.rp_session = requests.Session()
        retries = Retry(total=5, backoff_factor=1, status_forcelist=[408, 429])
        self.rp_session.mount("http://", HTTPAdapter(max_retries=retries))

        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        self.endpoint_url_base = runpod.endpoint_url_base

    def _request(
        self, method: str, endpoint: str, data: Optional[dict] = None, 
        timeout: int = 10, api_key: Optional[str] = None
    ):
        """
        Make a request to the specified endpoint using the given HTTP method.

        Args:
            method: The HTTP method to use ('GET' or 'POST').
            endpoint: The endpoint path to which the request will be made.
            data: The JSON payload to send with the request.
            timeout: The number of seconds to wait for the server to send data before giving up.
            api_key: Optional API key to use for this specific request.

        Returns:
            The JSON response from the server.

        Raises:
            ValueError: If request API key conflicts with instance API key.
            RuntimeError: If the response returns a 401 Unauthorized status.
            requests.HTTPError: If the response contains an unsuccessful status code.
        """
        # Check for conflicting API keys
        if api_key and self.api_key and api_key != self.api_key:
            raise ValueError(
                "Conflicting API keys: Request API key differs from instance API key. "
                "Use only one API key source to avoid security issues."
            )
        
        # Use request-specific API key if provided, otherwise use instance API key
        effective_api_key = api_key or self.api_key
        headers = self.headers if not api_key else {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {effective_api_key}",
        }
        
        url = f"{self.endpoint_url_base}/{endpoint}"
        response = self.rp_session.request(
            method, url, headers=headers, json=data, timeout=timeout
        )

        if response.status_code == 401:
            raise RuntimeError(UNAUTHORIZED_MSG)

        response.raise_for_status()
        return response.json()

    def post(self, endpoint: str, data: dict, timeout: int = 10, api_key: Optional[str] = None):
        """Post to the endpoint with optional API key override."""
        return self._request("POST", endpoint, data, timeout, api_key=api_key)

    def get(self, endpoint: str, timeout: int = 10, api_key: Optional[str] = None):
        """Get from the endpoint with optional API key override."""
        return self._request("GET", endpoint, timeout=timeout, api_key=api_key)


# ---------------------------------------------------------------------------- #
#                                      Job                                     #
# ---------------------------------------------------------------------------- #
class Job:
    """Represents a job to be run on the Runpod service."""

    def __init__(self, endpoint_id: str, job_id: str, client: RunPodClient, 
                 api_key: Optional[str] = None):
        """
        Initialize a Job instance with the given endpoint ID and job ID.

        Args:
            endpoint_id: The identifier for the endpoint.
            job_id: The identifier for the job.
            client: An instance of the RunPodClient to make requests with.
            api_key: Optional API key for this specific job.
        """
        self.endpoint_id = endpoint_id
        self.job_id = job_id
        self.rp_client = client
        self.api_key = api_key  # Store job-specific API key

        self.job_status = None
        self.job_output = None

    def _fetch_job(self, source: str = "status") -> Dict[str, Any]:
        """Returns the raw json of the status, raises an exception if invalid"""
        status_url = f"{self.endpoint_id}/{source}/{self.job_id}"
        # Pass the job-specific API key if available
        job_state = self.rp_client.get(endpoint=status_url, api_key=self.api_key)

        if is_completed(job_state["status"]):
            self.job_status = job_state["status"]
            self.job_output = job_state.get("output", None)

        return job_state

    def status(self):
        """Returns the status of the job request."""
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

    def stream(self) -> Any:
        """Returns a generator that yields the output of the job request."""
        while True:
            time.sleep(1)
            stream_partial = self._fetch_job(source="stream")
            if (
                stream_partial["status"] not in FINAL_STATES
                or len(stream_partial["stream"]) > 0
            ):
                for chunk in stream_partial.get("stream", []):
                    yield chunk["output"]
            elif stream_partial["status"] in FINAL_STATES:
                break

    def cancel(self, timeout: int = 3) -> Any:
        """
        Cancels the job and returns the result of the cancellation request.

        Args:
            timeout: The number of seconds to wait for the server to respond before giving up.
        """
        return self.rp_client.post(
            f"{self.endpoint_id}/cancel/{self.job_id}", 
            data=None, 
            timeout=timeout,
            api_key=self.api_key
        )


# ---------------------------------------------------------------------------- #
#                                   Endpoint                                   #
# ---------------------------------------------------------------------------- #
class Endpoint:
    """Manages an endpoint to run jobs on the Runpod service."""

    def __init__(self, endpoint_id: str, api_key: Optional[str] = None):
        """
        Initialize an Endpoint instance with the given endpoint ID.

        Args:
            endpoint_id: The identifier for the endpoint.
            api_key: Optional API key for this endpoint instance.

        Example:
            >>> endpoint = runpod.Endpoint("ENDPOINT_ID")
            >>> run_request = endpoint.run({"your_model_input_key": "your_model_input_value"})
            >>> print(run_request.status())
            >>> print(run_request.output())
        """
        self.endpoint_id = endpoint_id
        self.rp_client = RunPodClient(api_key=api_key)

    def run(self, request_input: Dict[str, Any], api_key: Optional[str] = None) -> Job:
        """
        Run the endpoint with the given input.

        Args:
            request_input: The input to pass into the endpoint.
            api_key: Optional API key to use for this specific request.

        Returns:
            A Job instance for the run request.
        """
        if not request_input.get("input"):
            request_input = {"input": request_input}

        job_request = self.rp_client.post(
            f"{self.endpoint_id}/run", 
            request_input,
            api_key=api_key
        )
        return Job(self.endpoint_id, job_request["id"], self.rp_client, api_key=api_key)

    def run_sync(
        self, request_input: Dict[str, Any], timeout: int = 86400, 
        api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Run the endpoint with the given input synchronously.

        Args:
            request_input: The input to pass into the endpoint.
            timeout: Maximum time to wait for the job to complete.
            api_key: Optional API key to use for this specific request.
        """
        if not request_input.get("input"):
            request_input = {"input": request_input}

        job_request = self.rp_client.post(
            f"{self.endpoint_id}/runsync", 
            request_input, 
            timeout=timeout,
            api_key=api_key
        )

        if job_request["status"] in FINAL_STATES:
            return job_request.get("output", None)

        return Job(
            self.endpoint_id, job_request["id"], self.rp_client, api_key=api_key
        ).output(timeout=timeout)

    def health(self, timeout: int = 3, api_key: Optional[str] = None) -> Dict[str, Any]:
        """
        Check the health of the endpoint (number/state of workers, number/state of requests).

        Args:
            timeout: The number of seconds to wait for the server to respond before giving up.
            api_key: Optional API key to use for this specific request.
        """
        return self.rp_client.get(
            f"{self.endpoint_id}/health", 
            timeout=timeout, 
            api_key=api_key
        )

    def purge_queue(self, timeout: int = 3, api_key: Optional[str] = None) -> Dict[str, Any]:
        """
        Purges the endpoint's job queue and returns the result of the purge request.

        Args:
            timeout: The number of seconds to wait for the server to respond before giving up.
            api_key: Optional API key to use for this specific request.
        """
        return self.rp_client.post(
            f"{self.endpoint_id}/purge-queue", 
            data=None, 
            timeout=timeout,
            api_key=api_key
        )
