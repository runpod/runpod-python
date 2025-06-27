"""
Runpod Tips
"""

import sys

import runpod.serverless.modules.rp_logger as RunPodLogger

log = RunPodLogger.RunPodLogger()


def check_return_size(return_body):
    """
    Checks the size of the return body.
    If the size is above 20MB, it will recommend using storage upload.
    """
    size_bytes = sys.getsizeof(return_body)
    size_mb = round(size_bytes / 1_000_000, 2)

    if size_mb > 20:
        log.tip(
            f"Your return body is {size_mb} MB which exceeds the 20 MB limit. "
            "Consider using S3 upload and returning the object's URL instead."
        )
