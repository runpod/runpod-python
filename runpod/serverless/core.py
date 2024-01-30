""" Core functionality for the runpod serverless worker. """

import ctypes
import inspect
import json
import os
import pathlib
import asyncio
from ctypes import CDLL, byref, c_char_p, c_int
from typing import Any, Callable,  List, Dict, Optional

from runpod.version import __version__ as runpod_version
from runpod.serverless.modules.rp_logger import RunPodLogger
from runpod.serverless.modules import rp_job

log = RunPodLogger()


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


class Hook:  # pylint: disable=too-many-instance-attributes
    """ Singleton class for interacting with sls_core.so"""

    _instance = None

    # C function pointers
    _get_jobs: Callable = None
    _progress_update: Callable = None
    _stream_output: Callable = None
    _post_output: Callable = None
    _finish_stream: Callable = None

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
            self.rust_so_path = os.environ.get("RUNPOD_SLS_CORE_PATH", str(default_path))
        else:
            self.rust_so_path = rust_so_path

        rust_library = CDLL(self.rust_so_path)
        buffer = ctypes.create_string_buffer(1024)  # 1 KiB
        num_bytes = rust_library._runpod_sls_crate_version(byref(buffer), c_int(len(buffer)))

        self.rust_crate_version = buffer.raw[:num_bytes].decode("utf-8")

        # Get Jobs
        self._get_jobs = rust_library._runpod_sls_get_jobs
        self._get_jobs.restype = CGetJobResult

        # Progress Update
        self._progress_update = rust_library._runpod_sls_progress_update
        self._progress_update.argtypes = [
            c_char_p, c_int,  # id_ptr, id_len
            c_char_p, c_int  # json_ptr, json_len
        ]
        self._progress_update.restype = c_int  # 1 if success, 0 if failure

        # Stream Output
        self._stream_output = rust_library._runpod_sls_stream_output
        self._stream_output.argtypes = [
            c_char_p, c_int,  # id_ptr, id_len
            c_char_p, c_int,  # json_ptr, json_len
        ]
        self._stream_output.restype = c_int  # 1 if success, 0 if failure

        # Post Output
        self._post_output = rust_library._runpod_sls_post_output
        self._post_output.argtypes = [
            c_char_p, c_int,  # id_ptr, id_len
            c_char_p, c_int,  # json_ptr, json_len
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
        buffer = ctypes.create_string_buffer(1024 * 1024 * 20)  # 20MB buffer to store jobs in
        destination_length = len(buffer.raw)
        result: CGetJobResult = self._get_jobs(
            c_int(max_concurrency), c_int(max_jobs),
            byref(buffer), c_int(destination_length)
        )
        if result.status_code == 1:  # success! the job was stored bytes 0..res_len of buf.raw
            return list(json.loads(buffer.raw[: result.res_len].decode("utf-8")))

        if result.status_code not in [0, 1]:
            raise RuntimeError(f"get_jobs failed with status code {result.status_code}")

        return []  # Status code 0, still waiting for jobs

    def progress_update(self, job_id: str, json_data: bytes) -> bool:
        """
        send a progress update to AI-API.
        """
        id_bytes = job_id.encode("utf-8")
        return bool(self._progress_update(
            c_char_p(id_bytes), c_int(len(id_bytes)),
            c_char_p(json_data), c_int(len(json_data))
        ))

    async def stream_output(self, job_id: str, job_output: bytes) -> bool:
        """
        send part of a streaming result to AI-API.
        """
        json_data = self._json_serialize_job_data(job_output)
        id_bytes = job_id.encode("utf-8")
        return bool(self._stream_output(
            c_char_p(id_bytes), c_int(len(id_bytes)),
            c_char_p(json_data), c_int(len(json_data))
        ))

    def post_output(self, job_id: str, job_output: bytes) -> bool:
        """
        send the result of a job to AI-API.
        Returns True if the task was successfully stored, False otherwise.
        """
        json_data = self._json_serialize_job_data(job_output)
        id_bytes = job_id.encode("utf-8")
        return bool(self._post_output(
            c_char_p(id_bytes), c_int(len(id_bytes)),
            c_char_p(json_data), c_int(len(json_data))
        ))

    def finish_stream(self, job_id: str) -> bool:
        """
        tell the SLS queue that the result of a streaming job is complete.
        """
        id_bytes = job_id.encode("utf-8")
        return bool(self._finish_stream(
            c_char_p(id_bytes), c_int(len(id_bytes))
        ))


# -------------------------------- Process Job ------------------------------- #
async def _process_job(config: Dict[str, Any], job: Dict[str, Any], hook) -> Dict[str, Any]:
    """ Process a single job. """
    handler = config['handler']

    result = {}
    try:
        if inspect.isgeneratorfunction(handler) or inspect.isasyncgenfunction(handler):
            log.debug("SLS Core | Running job as a generator.")
            generator_output = rp_job.run_job_generator(handler, job)
            aggregated_output = {'output': []}

            async for part in generator_output:
                log.debug(f"SLS Core | Streaming output: {part}", job['id'])

                if 'error' in part:
                    aggregated_output = part
                    break
                if config.get('return_aggregate_stream', False):
                    aggregated_output['output'].append(part['output'])

                await hook.stream_output(job['id'], part)

            log.debug("SLS Core | Finished streaming output.", job['id'])
            hook.finish_stream(job['id'])
            result = aggregated_output

        else:
            log.debug("SLS Core | Running job as a standard function.")
            result = await rp_job.run_job(handler, job)
            result = result.get('output', result)

    except Exception as err:  # pylint: disable=broad-except
        log.error(f"SLS Core | Error running job: {err}", job['id'])
        result = {'error': str(err)}

    finally:
        log.debug(f"SLS Core | Posting output: {result}", job['id'])
        hook.post_output(job['id'], result)


# ---------------------------------------------------------------------------- #
#                                  Run Worker                                  #
# ---------------------------------------------------------------------------- #
async def run(config: Dict[str, Any]) -> None:
    """ Run the worker.

    Args:
        config: A dictionary containing the following keys:
            handler: A function that takes a job and returns a result.
    """
    max_concurrency = config.get('max_concurrency', 1)
    max_jobs = config.get('max_jobs', 1)

    serverless_hook = Hook()

    while True:
        jobs = serverless_hook.get_jobs(max_concurrency, max_jobs)

        if len(jobs) == 0 or jobs is None:
            await asyncio.sleep(0)
            continue

        for job in jobs:
            asyncio.create_task(_process_job(config, job, serverless_hook), name=job['id'])
            await asyncio.sleep(0)

        await asyncio.sleep(0)


def main(config: Dict[str, Any]) -> None:
    """Run the worker in an asyncio event loop."""
    if config.get('handler') is None:
        log.error("SLS Core | config must contain a handler function")
        raise ValueError("config must contain a handler function")

    try:
        work_loop = asyncio.new_event_loop()
        asyncio.ensure_future(run(config), loop=work_loop)
        work_loop.run_forever()
    finally:
        work_loop.close()
