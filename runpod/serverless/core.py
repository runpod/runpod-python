""" Core functionality for the runpod serverless worker. """

import asyncio
import ctypes
import inspect
import json
import os
import pathlib
import typing
from ctypes import CDLL, byref, c_char_p, c_int
from typing import Any, Callable, Dict, List, Optional

from runpod.serverless.modules import rp_job
from runpod.serverless.modules.rp_logger import RunPodLogger
from runpod.version import __version__ as runpod_version

log = RunPodLogger()

# _runpod_sls_get_jobs status codes
STILL_WAITING = 0 
OK = 1
ERROR_FROM_SERVER = 2
ERROR_BUFFER_TOO_SMALL = 3

class CGetJobResult(ctypes.Structure):  # pylint: disable=too-few-public-methods
    """
     result of _runpod_sls_get_jobs.
    ## fields
    - `res_len` the number bytes were written to the `dst_buf` passed to _runpod_sls_get_jobs.
    - `status_code` tells you what happened.
    see CGetJobResult.status_code for more information.
    """

    _fields_ = [("status_code", ctypes.c_int), ("res_len", ctypes.c_int)]

    def __str__(self) -> str:
        return f"CGetJobResult(res_len={self.res_len}, status_code={self.status_code})"


def notregistered():
    """Function to raise NotImplementedError"""
    raise RuntimeError("This function is not registered with the SLS Core.")

class SlsCoreError(Exception):
    pass

class Hook:  # pylint: disable=too-many-instance-attributes
    """Singleton class for interacting with sls_core.so"""

    _instance = None

    # C function pointers
    _get_jobs: Callable = notregistered
    _progress_update: Callable = notregistered
    _stream_output: Callable = notregistered
    _post_output: Callable = notregistered
    _finish_stream: Callable = notregistered

    def __new__(cls):
        if Hook._instance is None:
            log.debug("SLS Core | Initializing Hook.")
            Hook._instance = object.__new__(cls)
            Hook._initialized = False

        return Hook._instance

    def __init__(self, rust_so_path: Optional[str] = None) -> None:

        if self._initialized:
            return

        if rust_so_path is None:
            default_path = os.path.join(
                pathlib.Path(__file__).parent.absolute(), "sls_core.so"
            )
            self.rust_so_path = os.environ.get(
                "RUNPOD_SLS_CORE_PATH", str(default_path)
            )
        else:
            self.rust_so_path = rust_so_path

        rust_library = CDLL(self.rust_so_path)
        buffer = ctypes.create_string_buffer(1024)  # 1 KiB
        num_bytes = rust_library._runpod_sls_crate_version(
            byref(buffer), c_int(len(buffer))
        )

        self.rust_crate_version = buffer.raw[:num_bytes].decode("utf-8")

        # Get Jobs
        self._get_jobs = rust_library._runpod_sls_get_jobs
        self._get_jobs.restype = CGetJobResult

        # Progress Update
        self._progress_update = rust_library._runpod_sls_progress_update
        self._progress_update.argtypes = [
            c_char_p,
            c_int,  # id_ptr, id_len
            c_char_p,
            c_int,  # json_ptr, json_len
        ]
        self._progress_update.restype = c_int  # 1 if success, 0 if failure

        # Stream Output
        self._stream_output = rust_library._runpod_sls_stream_output
        self._stream_output.argtypes = [
            c_char_p,
            c_int,  # id_ptr, id_len
            c_char_p,
            c_int,  # json_ptr, json_len
        ]
        self._stream_output.restype = c_int  # 1 if success, 0 if failure

        # Post Output
        self._post_output = rust_library._runpod_sls_post_output
        self._post_output.argtypes = [
            c_char_p,
            c_int,  # id_ptr, id_len
            c_char_p,
            c_int,  # json_ptr, json_len
        ]
        self._post_output.restype = c_int  # 1 if success, 0 if failure

        # Finish Stream
        self._finish_stream = rust_library._runpod_sls_finish_stream
        self._finish_stream.argtypes = [c_char_p, c_int]  # id_ptr, id_len
        self._finish_stream.restype = c_int  # 1 if success, 0 if failure

        rust_library._runpod_sls_crate_version.restype = c_int

        rust_library._runpod_sls_init.argtypes = []
        rust_library._runpod_sls_init.restype = c_int
        rust_library._runpod_sls_init()

        self._initialized = True

    def _json_serialize_job_data(self, job_data: Any) -> bytes:
        return json.dumps(job_data, ensure_ascii=False).encode("utf-8")

    def get_jobs(self, max_concurrency: int, max_jobs: int) -> List[Dict[str, Any]]:
        """Get a job or jobs from the queue. The jobs are returned as a list of Job objects."""
        buf = ctypes.create_string_buffer(
            1024 * 1024 * 20
        )  # 20MB buffer to store jobs in
        destination_length = len(buf.raw)
        res: CGetJobResult = self._get_jobs(
            c_int(max_concurrency),
            c_int(max_jobs),
            byref(buf),
            c_int(destination_length),
        )
        n = res.res_len
        code = res.status_code
        if code == STILL_WAITING:
            return []  # still waiting for jobs
        elif code == OK:  # success! the job was stored bytes 0..res_len of buf.raw
            log.trace(f"decoding {n} bytes of JSON")
            return list(json.loads(buf.raw[:n].decode("utf-8")))
        elif code == ERROR_FROM_SERVER:
            try:
                b = buf.raw[: res.res_len].decode("utf-8")
            except Exception:
                b = "<failed to decode buffer>"
            if b == "":
                b = "<unknown error or buffer too small>"
            raise SlsCoreError(f"_runpod_sls_get_jobs: status code 2: error from server: {b}")
        elif code == ERROR_BUFFER_TOO_SMALL:  # buffer too small
            raise SlsCoreError("_runpod_sls_get_jobs: status code 3: buffer too small")
        else:
            raise ValueError(f"_runpod_sls_get_jobs: unknown status code {code}")


    def progress_update(self, job_id: str, json_data: bytes) -> bool:
        """
        send a progress update to AI-API.
        """
        id_bytes = job_id.encode("utf-8")
        return bool(
            self._progress_update(
                c_char_p(id_bytes),
                c_int(len(id_bytes)),
                c_char_p(json_data),
                c_int(len(json_data)),
            )
        )

    async def stream_output(self, job_id: str, job_output: bytes) -> bool:
        """
        send part of a streaming result to AI-API.
        """
        json_data = self._json_serialize_job_data(job_output)
        id_bytes = job_id.encode("utf-8")
        return bool(
            self._stream_output(
                c_char_p(id_bytes),
                c_int(len(id_bytes)),
                c_char_p(json_data),
                c_int(len(json_data)),
            )
        )

    def post_output(self, job_id: str, job_output: bytes) -> bool:
        """
        send the result of a job to AI-API.
        Returns True if the task was successfully stored, False otherwise.
        """
        json_data = self._json_serialize_job_data(job_output)
        id_bytes = job_id.encode("utf-8")
        return bool(
            self._post_output(
                c_char_p(id_bytes),
                c_int(len(id_bytes)),
                c_char_p(json_data),
                c_int(len(json_data)),
            )
        )

    def finish_stream(self, job_id: str) -> bool:
        """
        tell the SLS queue that the result of a streaming job is complete.
        """
        id_bytes = job_id.encode("utf-8")
        return bool(self._finish_stream(c_char_p(id_bytes), c_int(len(id_bytes))))


# -------------------------------- Process Job ------------------------------- #
async def _process_job(
    config: Dict[str, Any], job: Dict[str, Any], hook
) -> Dict[str, Any]:
    """Process a single job."""
    handler = config["handler"]

    result = {}
    try:
        if inspect.isgeneratorfunction(handler) or inspect.isasyncgenfunction(handler):
            log.debug("SLS Core | Running job as a generator.")
            generator_output = rp_job.run_job_generator(handler, job)
            aggregated_output: dict[str, typing.Any] = {"output": []}

            async for part in generator_output:
                log.trace(f"SLS Core | Streaming output: {part}", job["id"])

                if "error" in part:
                    aggregated_output = part
                    break
                if config.get("return_aggregate_stream", False):
                    aggregated_output["output"].append(part["output"])

                await hook.stream_output(job["id"], part)

            log.debug("SLS Core | Finished streaming output.", job["id"])
            hook.finish_stream(job["id"])
            result = aggregated_output

        else:
            log.debug("SLS Core | Running job as a standard function.")
            result = await rp_job.run_job(handler, job)
            result = result.get("output", result)

    except Exception as err:  # pylint: disable=broad-except
        log.error(f"SLS Core | Error running job: {err}", job["id"])
        result = {"error": str(err)}

    finally:
        log.debug(f"SLS Core | Posting output: {result}", job["id"])
        hook.post_output(job["id"], result)
        return result


# ---------------------------------------------------------------------------- #
#                                  Run Worker                                  #
# ---------------------------------------------------------------------------- #
async def run(config: Dict[str, Any]) -> None:
    """Run the worker.

    Args:
        config: A dictionary containing the following keys:
            handler: A function that takes a job and returns a result.
    """
    max_concurrency = config.get("max_concurrency", 1)
    max_jobs = config.get("max_jobs", 1)

    serverless_hook = Hook()
    while True:
        try:
            jobs = serverless_hook.get_jobs(max_concurrency, max_jobs)
        except SlsCoreError as err:
            log.error(f"SLS Core | Error getting jobs: {err}")
            await asyncio.sleep(0.2) # sleep for a bit before trying again
            continue

        if len(jobs) == 0 or jobs is None:
            await asyncio.sleep(0)
            continue

        for job in jobs:
            asyncio.create_task(
                _process_job(config, job, serverless_hook), name=job["id"]
            )
            await asyncio.sleep(0)

        await asyncio.sleep(0)


def main(config: Dict[str, Any]) -> None:
    """Run the worker in an asyncio event loop."""
    if config.get("handler") is None:
        log.error("SLS Core | config must contain a handler function")
        raise ValueError("config must contain a handler function")

    try:
        work_loop = asyncio.new_event_loop()
        asyncio.ensure_future(run(config), loop=work_loop)
        work_loop.run_forever()
    finally:
        work_loop.close()
