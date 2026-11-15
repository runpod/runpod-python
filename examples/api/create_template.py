"""Example of creating a template with the Runpod API.

The template stays on your account after the script exits. Delete it from
the Templates page in the console when you no longer need it.
"""

import uuid

import runpod

# Run `rp login` once to store your API key, or uncomment the line below:
# runpod.api_key = "YOUR_RUNPOD_API_KEY"

# Template names must be unique on your account.
TEMPLATE_NAME = f"example-template-{uuid.uuid4().hex[:8]}"

try:

    new_template = runpod.create_template(
        name=TEMPLATE_NAME, image_name="runpod/base:2.0.0-ubuntu2404"
    )

    print(new_template)

except runpod.error.QueryError as err:
    print(err)
    print(err.query)
