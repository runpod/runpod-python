""" Example of creating an endpoint with the Runpod API. """

import runpod

# Run `rp login` once to store your API key, or uncomment the line below:
# runpod.api_key = "YOUR_RUNPOD_API_KEY"

try:

    new_template = runpod.create_template(
        name="test", image_name="runpod/mock-worker:latest", is_serverless=True
    )

    print(new_template)

    new_endpoint = runpod.create_endpoint(
        name="test",
        template_id=new_template["id"],
        gpu_ids="AMPERE_16",
        workers_min=0,
        workers_max=1,
        flashboot=True,
    )

    print(new_endpoint)

except runpod.error.QueryError as err:
    print(err)
    print(err.query)
