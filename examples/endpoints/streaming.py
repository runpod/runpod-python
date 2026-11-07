""" Example of streaming data from an endpoint. """

import runpod

# Run `rp login` once to store your API key, or uncomment the line below:
# runpod.api_key = "YOUR_RUNPOD_API_KEY"

# This input targets an endpoint running the runpod/mock-worker image started
# with --generator, which yields each item of mock_return with mock_delay
# seconds between them. Replace it with the input your own streaming
# handler expects.
endpoint = runpod.Endpoint("YOUR_ENDPOINT_ID")

run_request = endpoint.run(
    {
        "input": {
            "mock_return": ["a", "b", "c", "d", "e", "f", "g"],
            "mock_delay": 1,
        }
    }
)

for output in run_request.stream():
    print(output)
