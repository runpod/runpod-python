## Logging

The worker outputs logs to the console at different points in the workers lifecycle. These logs can be used to debug issues with the worker or handler. There are four logging levels that can be used to control the verbosity of the logs:

   0. `NOTSET` - Does not output any logs.

   1. `DEBUG` (Default) - Outputs all logs, including debug logs.

   2. `INFO` - Outputs all logs except debug logs.

   3. `WARNING` - Outputs only warning and error logs.

   4. `ERROR` - Outputs only error logs.

### Setting the Logging Level

There are two ways to set the logging level:

   1. Set the `RUNPOD_DEBUG_LEVEL` environment variable to one of the above logging levels.

   2. Set the `rp_log_level` argument when calling the file with your handler. If this value is set, it will override the `RUNPOD_DEBUG_LEVEL` environment variable.

        ```python
        python worker.py --rp_log_level='INFO'
        ```
