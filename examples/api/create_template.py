""" Example of creating a template with the Runpod API. """

import runpod

# Run `rp login` once to store your API key, or uncomment the line below:
# runpod.api_key = "YOUR_RUNPOD_API_KEY"

try:

    new_template = runpod.create_template(
        name="test", image_name="runpod/base:2.0.0-ubuntu2404"
    )

    print(new_template)

except runpod.error.QueryError as err:
    print(err)
    print(err.query)
