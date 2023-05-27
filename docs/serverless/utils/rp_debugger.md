# runpod.serverless.rp_debugger

The `runpod.serverless.rp_debugger` module provides a set of classes and functions to help with debugging your serverless functions. To enable the debugger and return the results with you job output, set the argument flag `--rp_debugger` when launching the file containing your serverless handler.

## System Info

This script automatically gathers system information including the Operating System, Processor, and Python version.

## Checkpoints

This is a singleton class to store checkpoint times. Each checkpoint records the start and end time (as `perf_counter` time and as UTC time), and calculates the duration in milliseconds. You can add, start, and stop checkpoints using the methods `add`, `start`, and `stop`.

### Example Usage:

```python
from rp_debugger import Checkpoints

checkpoints = Checkpoints()

# Add a checkpoint
checkpoints.add('checkpoint_name')

# Start a checkpoint
checkpoints.start('checkpoint_name')

# Stop a checkpoint
checkpoints.stop('checkpoint_name')
```

## LineTimer

This is a context manager that you can use with the `with` statement to time the execution of a specific block of code. When used, the times should be added to the `Checkpoints` object.

### Example Usage:

```python
from rp_debugger import LineTimer

with LineTimer('my_block_of_code'):
    # Your code here
    pass
```

## FunctionTimer

This is a class-based decorator that you can use to measure the time it takes for a function to execute.

### Example Usage:

```python
from rp_debugger import FunctionTimer

@FunctionTimer
def my_function():
    # Your code here
    pass
```

## get_debugger_output

This function returns the debugger output, including system information and timestamps of all the checkpoints.

### Example Usage:

```python
from rp_debugger import get_debugger_output

output = get_debugger_output()
```

## Example with all combined:

```python
from rp_debugger import Checkpoints, LineTimer, FunctionTimer, get_debugger_output

checkpoints = Checkpoints()

checkpoints.add('checkpoint_name')
checkpoints.start('checkpoint_name')

with LineTimer('my_block_of_code'):
    # Your code here
    pass

checkpoints.stop('checkpoint_name')

@FunctionTimer
def my_function():
    # Your code here
    pass

my_function()

output = get_debugger_output()

print(output)
```

In the above example, `output` will be a dictionary containing system information and checkpoint timings. The `Checkpoints` object can be reused multiple times across your code, and the timings will be aggregated until you call `get_debugger_output`, at which point they will be cleared.
