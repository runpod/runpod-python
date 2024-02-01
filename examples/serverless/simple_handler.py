""" Simple Handler

To setup a local API server, run the following command:
python simple_handler.py --rp_serve_api
"""

import runpod


def handler(job):
    """Simple handler"""
    job_input = job["input"]

    return f"Hello {job_input['name']}!"


runpod.serverless.start({"handler": handler})
