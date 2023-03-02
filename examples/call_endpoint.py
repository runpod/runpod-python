'''
Example of calling an endpoint using the RunPod Python Language Library.
'''

import runpod

runpod.api_key = "YOUR_API_KEY"
endpoint = runpod.Endpoint("ENDPOINT_ID")

run_request = endpoint.run(
    {"YOUR_MODEL_INPUT_JSON": "YOUR_MODEL_INPUT_VALUE"}
)

# Check the status of the endpoint run request
print(run_request.status())

# Get the output of the endpoint run request, blocking until the endpoint run is complete.
print(run_request.output())
