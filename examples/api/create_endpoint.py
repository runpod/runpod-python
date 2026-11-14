"""Example of creating an endpoint with the Runpod API.

The endpoint scales to zero workers when idle, so it costs nothing until it
receives requests. It stays on your account after the script exits; delete it
with `rp endpoint delete ENDPOINT_ID`, then delete its template from the
Templates page in the console.
"""

import uuid

import runpod

# Run `rp login` once to store your API key, or uncomment the line below:
# runpod.api_key = "YOUR_RUNPOD_API_KEY"

# Template and endpoint names must be unique on your account.
NAME = f"example-endpoint-{uuid.uuid4().hex[:8]}"

try:

    # runpod/mock-worker echoes its input back, which is enough to try the
    # examples in examples/endpoints/.
    new_template = runpod.create_template(
        name=NAME, image_name="runpod/mock-worker:latest", is_serverless=True
    )

    print(new_template)

    new_endpoint = runpod.create_endpoint(
        name=NAME,
        template_id=new_template["id"],
        gpu_ids="AMPERE_16",
        workers_min=0,
        workers_max=1,
        flashboot=True,
    )

    print(new_endpoint)
    print(f"Endpoint id: {new_endpoint['id']}")
    print(f"Delete it with: rp endpoint delete {new_endpoint['id']}")

except runpod.error.QueryError as err:
    print(err)
    print(err.query)
