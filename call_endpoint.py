import runpod

runpod.api_key = "A30XBFAQVFY3SPRRTHF4GGPU6D3SWOIZYG13ICUT"
endpoint = runpod.Endpoint("stable-diffusion-v1")

run_request = endpoint.run(
    {"prompt": "The quick brown fox jumps over the lazy dog."}
)

# Check the status of the endpoint run request
print(run_request.status())

# Get the output of the endpoint run request, blocking until the endpoint run is complete.
print(run_request.output())
