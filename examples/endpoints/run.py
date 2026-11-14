"""
Example of calling an endpoint using the Runpod Python Language Library.
"""

import runpod

# Run `rp login` once to store your API key, or uncomment the line below:
# runpod.api_key = "YOUR_RUNPOD_API_KEY"

endpoint = runpod.Endpoint("YOUR_ENDPOINT_ID")

run_request = endpoint.run(
    {"input": {"prompt": "a photo of a horse the size of a Boeing 787"}}
)

# Check the status of the run request, for example IN_QUEUE or IN_PROGRESS.
print(run_request.status())

# Wait up to 60 seconds for the job to finish, then print its output.
# Raises TimeoutError if the job is still running after that.
# Without a timeout, output() returns immediately, and returns None if the
# job has not finished yet.
print(run_request.output(timeout=60))
