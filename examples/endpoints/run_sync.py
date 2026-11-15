"""
Example of calling an endpoint using the Runpod Python Language Library.
"""

import requests

import runpod

# Run `rp login` once to store your API key, or uncomment the line below:
# runpod.api_key = "YOUR_RUNPOD_API_KEY"

endpoint = runpod.Endpoint("YOUR_ENDPOINT_ID")

try:
    # Run the endpoint synchronously, blocking until the endpoint run is complete.
    run_request = endpoint.run_sync(
        {"input": {"prompt": "a photo of a horse the size of a Boeing 787"}},
        timeout=60,  # Seconds
    )

    print(run_request)
except (TimeoutError, requests.exceptions.Timeout):
    # TimeoutError: the job was still running when the timeout expired.
    # requests.exceptions.Timeout: the HTTP request itself timed out.
    print("Job timed out.")
