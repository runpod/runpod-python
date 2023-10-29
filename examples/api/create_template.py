""" Example of creating a template with the Runpod API. """

import runpod

# Set your global API key with `runpod config` or uncomment the line below:
# runpod.api_key = "YOUR_RUNPOD_API_KEY"

try:

    new_template = runpod.create_template(
        name="test",
        image_name="runpod/base:0.1.0"
    )

    print(new_template)

except runpod.error.QueryError as err:
    print(err)
    print(err.query)
