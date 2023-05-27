# rp_debugger.py

The `rp_debugger.py` module provides a collection of debugging tools for the RunPod serverless platform. It includes a `Checkpoints` class for time management, a decorator for function benchmarking, and a function to fetch debugger output.

Here's how you can use them in your code:

## Checkpoints

The `Checkpoints` class is a singleton that provides a way to manage and store timestamps associated with named checkpoints.

```python
from rp_debugger import Checkpoints

# Initialize Checkpoints
checkpoints = Checkpoints()

# Add a checkpoint
checkpoints.add('checkpoint_name')

# Start the checkpoint
checkpoints.start('checkpoint_name')

# Stop the checkpoint
checkpoints.stop('checkpoint_name')

# Get the results
results = checkpoints.get_checkpoints()
```

### Methods

- `add(name: str)`: Adds a new checkpoint with the given name.

- `start(name: str)`: Starts the timing for the checkpoint with the given name.

- `stop(name: str)`: Stops the timing for the checkpoint with the given name.

- `get_checkpoints()`: Returns the list of checkpoints, including their durations.

## benchmark(function)

This is a decorator that can be used to automatically time a function's execution.

```python
from rp_debugger import benchmark

@benchmark
def my_function():
    # Your code here
```

The `benchmark` decorator automatically starts and stops a checkpoint with the same name as the function, and adds the duration to the checkpoints list.

## get_debugger_output()

This function returns the current state of the debugger, which includes a list of all checkpoints and their durations. You can call this function at any point in your code to see the current debugger state.

```python
from rp_debugger import get_debugger_output

debugger_output = get_debugger_output()
```

This would return the list of checkpoints with their respective durations.
