"""
runpod | serverless | rp_debugger.py

A collection of functions to help with debugging.
"""

import datetime
from datetime import timezone
import functools
import platform
import time

# ---------------------------------------------------------------------------- #
#                                  System Info                                 #
# ---------------------------------------------------------------------------- #
OS_INFO = f"{platform.system()} {platform.release()}"

PROCESSOR_UNKNOWN = "Unable to get processor info."


@functools.cache
def get_processor_info():
    """
    Return the processor brand name, probing the CPU on first call only.

    The probe takes ~75ms, so it is kept off the worker import path and only
    runs when a job requests debugger output.
    """
    import cpuinfo  # pylint: disable=import-outside-toplevel

    return cpuinfo.get_cpu_info().get("brand_raw", PROCESSOR_UNKNOWN)


PYTHON_VERSION = platform.python_version()


def _start_entry(entry):
    entry["start"] = time.perf_counter()
    entry["start_utc"] = datetime.datetime.now(timezone.utc).isoformat() + "Z"


def _stop_entry(entry):
    entry["end"] = time.perf_counter()
    entry["stop_utc"] = datetime.datetime.now(timezone.utc).isoformat() + "Z"


class Checkpoints:
    """
    A singleton class to store checkpoint times.

    Format:
    [
        {
            'name': 'checkpoint_name',
            'start': 1234567890.123456,
            'end': 1234567890.123456
            'duration_ms': 1234567890.123456
        },
    ]

    Usage:
    from rp_debugger import Checkpoints

    checkpoints = Checkpoints()

    # Add a checkpoint
    checkpoints.add('checkpoint_name')

    # Start a checkpoint
    checkpoints.start('checkpoint_name')

    # Stop a checkpoint
    checkpoints.stop('checkpoint_name')
    """

    __instance = None
    checkpoints = []
    name_lookup = {}

    def __new__(cls):
        if Checkpoints.__instance is None:
            Checkpoints.__instance = object.__new__(cls)
            Checkpoints.__instance.checkpoints = []
            Checkpoints.__instance.name_lookup = {}
        return Checkpoints.__instance

    def add(self, name):
        """
        Add a checkpoint.
        Returns the index of the checkpoint.
        """
        if name in self.name_lookup:
            raise KeyError(f'Checkpoint name "{name}" already exists.')

        self.checkpoints.append({"name": name})

        index = len(self.checkpoints) - 1
        self.name_lookup[name] = index

    def start(self, name):
        """
        Start a checkpoint.
        """
        if name not in self.name_lookup:
            raise KeyError(f"Checkpoint name '{name}' does not exist.")

        _start_entry(self.checkpoints[self.name_lookup[name]])

    def stop(self, name):
        """
        Stop a checkpoint.
        """
        if name not in self.name_lookup:
            raise KeyError(f"Checkpoint name '{name}' does not exist.")

        index = self.name_lookup[name]

        if "start" not in self.checkpoints[index]:
            raise KeyError("Checkpoint has not been started.")

        _stop_entry(self.checkpoints[index])

    def new_timer_entry(self, name):
        """
        Append a checkpoint for a timer and return it.

        Unlike add(), the name may repeat. A worker running several jobs at once
        (concurrency_modifier) can time the same function or block in each job,
        and every timer starts and stops its own entry, so neither a duplicate
        name nor a clear() from a job that finished first raises.
        """
        entry = {"name": name}
        self.checkpoints.append(entry)
        return entry

    def get_checkpoints(self):
        """
        Get the results of the checkpoints.
        """
        results = []
        for checkpoint in self.checkpoints:
            if "start" not in checkpoint or "end" not in checkpoint:
                continue
            start_time = checkpoint["start"]
            end_time = checkpoint["end"]
            checkpoint["duration_ms"] = (end_time - start_time) * 1000

            checkpoint.pop("start")
            checkpoint.pop("end")

            results.append(checkpoint)

        return results

    def clear(self):
        """
        Clear the checkpoints.
        """
        self.checkpoints = []
        self.name_lookup = {}


class LineTimer:
    """
    A utility that can be used to time code execution using the with statement.
    When used the times should be added to the checkpoints object.
    """

    def __init__(self, name):
        self.checkpoints = Checkpoints()
        self.name = name
        self.entry = None

    def __enter__(self):
        self.entry = self.checkpoints.new_timer_entry(self.name)
        _start_entry(self.entry)

    def __exit__(self, *args):
        _stop_entry(self.entry)


class FunctionTimer:  # pylint: disable=too-few-public-methods
    """
    A class-based decorator to benchmark a function.
    """

    def __init__(self, function):
        self.function = function
        self.checkpoints = Checkpoints()

    def __call__(self, *args, **kwargs):
        entry = self.checkpoints.new_timer_entry(self.function.__name__)
        _start_entry(entry)

        try:
            return self.function(*args, **kwargs)
        finally:
            _stop_entry(entry)


def get_debugger_output():
    """
    Return the debugger output.
    """
    print("Getting debugger output...")
    import runpod  # pylint: disable=import-outside-toplevel, cyclic-import

    print("Getting checkpoints...")

    checkpoints = Checkpoints()
    ckpt_results = checkpoints.get_checkpoints()
    checkpoints.clear()

    print("Getting system info...")

    system_info = {
        "os": OS_INFO,
        "processor": get_processor_info(),
        "python_version": PYTHON_VERSION,
        "runpod": runpod.__version__,
    }

    print("Debugger output complete.")

    return {
        "system_info": system_info,
        "timestamps": ckpt_results,
    }


def clear_debugger_output():
    """
    Clear the debugger output.
    """
    checkpoints = Checkpoints()
    checkpoints.clear()
