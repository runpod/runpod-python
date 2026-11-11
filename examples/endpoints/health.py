""" Example of getting the health of an endpoint. """

import runpod

# Run `rp login` once to store your API key, or uncomment the line below:
# runpod.api_key = "YOUR_RUNPOD_API_KEY"

endpoint = runpod.Endpoint("YOUR_ENDPOINT_ID")

endpoint_health = endpoint.health()

print(endpoint_health)
