""" Example of creating a container registry auth with the Runpod API. """

import runpod

# Set your global API key with `runpod config` or uncomment the line below:
# runpod.api_key = "YOUR_RUNPOD_API_KEY"

try:
    new_container_registry_auth = runpod.create_container_registry_auth(
        name="test-container-registry-auth-name",
        username="test-username",
        password="test-password",
    )

    print(new_container_registry_auth)

except runpod.error.QueryError as err:
    print(err)
    print(err.query)
