""" Example of streaming data from an endpoint. """

import runpod

# Set your global API key with `runpod config` or uncomment the line below:
# runpod.api_key = "YOUR_RUNPOD_API_KEY"

endpoint = runpod.Endpoint("gwp4kx5yd3nur1")

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
