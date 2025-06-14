""" Simple Handler

To setup a local API server, run the following command:
python simple_handler.py --rp_serve_api
"""

import runpod


def handler(job):
    """Simple handler"""
    job_input = job["input"]

    return f"Hello {job_input['name']}!"


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
