'''
Example of calling an endpoint using the RunPod Python Language Library.
'''

import runpod

# Set your global API key with `runpod config` or uncomment the line below:
# runpod.api_key = "YOUR_RUNPOD_API_KEY"

endpoint = runpod.Endpoint("sdxl")  # Where "sdxl" is the endpoint ID

run_request = endpoint.run({
    "input": {
        "prompt": "a photo of a horse the size of a Boeing 787"
    }
})

# Check the status of the run request
print(run_request.status())

# Get the output of the endpoint run request.
print(run_request.output())

# Get the output of the endpoint run request.
# If timeout is greater than 0, this will block until the endpoint run is complete.
print(run_request.output(timeout=60))
