"""Example of listing your serverless endpoints with the Runpod API."""

import runpod

# Run `rp login` once to store your API key, or uncomment the line below:
# runpod.api_key = "YOUR_RUNPOD_API_KEY"

endpoints = runpod.get_endpoints()

# Each entry is the endpoint's full configuration, including its environment
# variables, so print only the id and name here.
for endpoint in endpoints:
    print(endpoint["id"], endpoint.get("name"))
