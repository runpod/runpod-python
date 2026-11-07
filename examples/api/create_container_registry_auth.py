"""Example of creating a container registry auth with the Runpod API.

Registry credentials let templates pull images from a private registry. The
credential stays on your account after the script exits; delete it with
`rp registry delete NAME`.
"""

import runpod

# Run `rp login` once to store your API key, or uncomment the line below:
# runpod.api_key = "YOUR_RUNPOD_API_KEY"

try:
    new_container_registry_auth = runpod.create_container_registry_auth(
        name="example-registry-auth",
        username="YOUR_REGISTRY_USERNAME",
        password="YOUR_REGISTRY_PASSWORD",
    )

    print(new_container_registry_auth)

except runpod.error.QueryError as err:
    print(err)
    print(err.query)
