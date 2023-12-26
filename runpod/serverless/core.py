import ctypes
import inspect
import json
import os
import pathlib
import sys
import traceback
from ctypes import CDLL, byref, c_char_p, c_int
from dataclasses import dataclass
from typing import Any, Callable,  List, TypeVar, Dict, Optional

import friendlyjson
import log

T = TypeVar("T")
O = TypeVar("O")


def debug(*args, **kwargs):
    print("DEBUG: ", *args, file=sys.stderr, **kwargs)


@dataclass
class Job:
    id: str
    input: Any
    status: str
    retries: int = 0


class CGetJobResult(ctypes.Structure):
    """
     result of _runpod_sls_get_jobs.
    ## fields
    - `res_len` tells you how many bytes were written to the `dst_buf` passed to _runpod_sls_get_jobs.
    - `status_code` tells you what happened.
    see CGetJobResult.status_code for more information.
    """

    _fields_ = [("status_code", ctypes.c_int), ("res_len", ctypes.c_int)]

    def __str__(self) -> str:
        return f"CGetJobResult(res_len={self.res_len}, status_code={self.status_code})"


class Hook:
    """ Singleton class for interacting with runpod_rust_sdk.so"""

    _instance = None

    # C function pointers
    _get_jobs: Callable = None
    _progress_update: Callable = None
    _stream_output: Callable = None
    _post_output: Callable = None
    _finish_stream: Callable = None

    def __new__(cls):
        if Hook._instance is None:
            Hook._instance = object.__new__(cls)
        return Hook._instance

    def __init__(self, rust_so_path: Optional[str] = None) -> None:
        if rust_so_path is None:
            default_path = pathlib.Path(__file__).parent / "runpod_rust_sdk.so"
            self.rust_so_path = os.environ.get("RUNPOD_RUST_SDK_PATH", str(default_path))
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

    def get_jobs(self, max_concurrency: int, max_jobs: int, *, json_decoder: Optional[Callable[[str], Any]] = None) -> List[Job]:
        """Get a job or jobs from the queue. The jobs are returned as a list of Job objects."""
        buffer = ctypes.create_string_buffer(1024 * 1024 * 20)  # 20MB
        destination_length = len(buffer.raw)
        result: CGetJobResult = self._get_jobs(c_int(max_concurrency), c_int(
            max_jobs), byref(buffer), c_int(destination_length))
        if result.status_code == 0:
            return []  # still waiting for jobs
        if result.status_code == 1:  # success! the job was stored bytes 0..res_len of buf.raw
            return [
                Job(id=d["id"], input=d["input"], status=d["status"], retries=d["retries"])
                for d in json_decoder(buffer.raw[: result.res_len].decode("utf-8"))
            ]

    def progress_update(self, job_id: str, json_data: bytes) -> bool:
        """
        send a progress update to AI-API.
        """
        id_bytes = job_id.encode("utf-8")
        return bool(self._progress_update(c_char_p(id_bytes), c_int(len(id_bytes)), c_char_p(json_data), c_int(len(json_data))))

    def stream_output(self, job_id: str, json_data: bytes) -> bool:
        """
        send part of a streaming result to AI-API.
        """
        id_bytes = job_id.encode("utf-8")
        return bool(self._stream_output(c_char_p(id_bytes), c_int(len(id_bytes)), c_char_p(json_data), c_int(len(json_data))))

    def post_output(self, job_id: str, json_data: bytes) -> bool:
        """
        send the result of a job to AI-API.
        Returns True if the task was successfully stored, False otherwise.
        """
        id_bytes = job_id.encode("utf-8")
        return bool(self._post_output(c_char_p(id_bytes), c_int(len(id_bytes)), c_char_p(json_data), c_int(len(json_data))))

    def finish_stream(self, job_id: str) -> bool:
        """
        tell the SLS queue that the result of a streaming job is complete.
        """
        id_bytes = job_id.encode("utf-8")
        return bool(self._finish_stream(c_char_p(id_bytes), c_int(len(id_bytes))))


def process_job(handler: Callable, job: Job) -> None:
    """process a job using the user-provided handler.
    This means:
    - deserialize the job data to the user-provided type
    - call the user-provided handler
    - serialize the result to JSON
    - store the result upstream by sending a POST to AI-API

    Success will remove the job from the SLS queue.
    Failure will be bubbled as a `ValueError` or `RuntimeError`:
    - a ValueError from issues on our end (e.g, failed to serialize the result to JSON, failed to post the result to AI-API)
    - a RuntimeError when bubbling up from user-provided handlers
    """
    hook = Hook()

    log.debug(f"processing job {job.id}")
    try:
        result = handler(job.input, id=job.id)
    except Exception as err:
        raise RuntimeError(
            f"run {job.id}: user code raised an {type(err).__name__}") from err

    if inspect.isgeneratorfunction(handler):
        # this is a STREAMED result, not a regular result.
        log.trace(f"process_job: job is a generator", job_id=job.id)
        i = 0
        for part in result:
            log.trace(f"run: stream: got part {i} of {job.id}", job_id=job.id)
            try:
                as_json = json.dumps(
                    part, cls=friendlyjson.Encoder).encode("utf-8")
            except Exception as e:
                raise ValueError(
                    f"failed to serialize result of {job.id} to JSON (a {type(result)}: {e}")
            if not hook.stream_output(job.id, as_json):
                raise ValueError(
                    "failed to stream output of part {i} of {job.id} to AI-API"
                )
            i += 1
            log.debug(f"run: stream: partial stream OK", job_id=job.id, part=i)
        else:  # no break
            log.debug(f"run: stream: finished streaming",
                      parts=i, job_id=job.id)
            hook.finish_stream(job.id)

        return
    # --- REGULAR RESULT ---
    try:
        as_json = json.dumps(result, cls=friendlyjson.Encoder).encode("utf-8")
    except Exception as e:
        raise ValueError(
            f"failed to serialize result of {job.id} to JSON (a {type(result)})"
        ) from e
    if not hook.post_output(job.id, as_json):
        raise ValueError(f"failed to post output of {job.id} to AI-API")


@dataclass
class ErrorInfo:
    error_message: str
    error_traceback: str
    error_type: str
    hostname: str
    job_id: Optional[str]
    runpod_version: str
    sdk_core_crate_version: str
    worker_id: str

    @classmethod
    def from_exception(cls, exc: Exception, id: Optional[str] = None) -> "ErrorInfo":
        return cls(
            error_type=type(exc).__name__,
            error_message=str(exc),
            error_traceback=traceback.format_exc(),
            job_id="unknown" if id is None else id,
            hostname=optional_envvar(
                "RUNPOD_POD_HOSTNAME", "unknown", log_frame_skip=4),
            worker_id=optional_envvar(
                "RUNPOD_WORKER_ID", "unknown", log_frame_skip=4),
            runpod_version=optional_envvar(
                "RUNPOD_VERSION", "unknown", log_frame_skip=4),
            sdk_core_crate_version=str(rust_crate_version),
        )


@dataclass
class RunResult:
    passed: List[str]
    failed: List[str]
    errors: Dict[str, ErrorInfo]

    def total_jobs(self) -> int:
        return len(self.passed) + len(self.failed)


def run(config: Dict[str, Any]) -> RunResult:
    """
    obtain up to max(max_concurrency, max_jobs) jobs from the SLS queue and process them using the given handler.
    - `handler`: the function to call for each job. it should take a single argument of type T and return a value of type O.
    - `max_concurrency`: maximum number of jobs to process at once. Must be >= 1.
    - `max_jobs`: the maximum number of jobs to process in total. Must be >= 1.
    - `max_retries`: the maximum number of times to retry a job before giving up. Must be >= 0.
    - `json_decoder`: a function that takes a string and returns an object. defaults to `json.loads`.

    """
    handler = config['handler']
    max_concurrency = config.get('max_concurrency', 4)
    max_jobs = config.get('max_jobs', 4)
    json_decoder = config.get('json_decoder', None)

    hook = Hook()

    while True:
        # bounds checking
        if not isinstance(max_concurrency, int) or not isinstance(max_jobs, int):
            raise TypeError(
                f"max_concurrency and max_jobs must be integers, got {type(max_concurrency)} and {type(max_jobs)}")
        if (max_concurrency < 1 or max_jobs < 1 or max_concurrency > 1024 or max_jobs > 1024):
            raise ValueError(
                f"{max_concurrency=} and {max_jobs=} must be between 1 and 1024")

        if json_decoder is None:
            json_decoder = json.loads

        try:
            jobs = hook.get_jobs(max_concurrency, max_jobs, json_decoder=json_decoder)
        except Exception as err:
            raise ValueError(f"failed to get jobs: {err}") from err

        if len(jobs) == 0:
            return RunResult(passed=[], failed=[], errors={})
        log.trace("got jobs", jobs=jobs)

        passed, failed, errors = [], [], {}
        for job in jobs:
            process_job(handler, job)

        res = RunResult(
            passed=passed,
            failed=failed,
            errors=errors,
        )
        log.trace("run: finished", jobs=res)
        return res
