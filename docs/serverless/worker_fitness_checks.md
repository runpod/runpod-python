# Worker Fitness Checks

Fitness checks allow you to validate your worker environment at startup before processing jobs. If any check fails, the worker exits immediately with an unhealthy status, allowing your container orchestrator to restart it or mark it as failed.

This is useful for validating:
- GPU availability and memory
- Required model files exist
- External service connectivity
- Disk space and system resources
- Environment configuration
- Any custom health requirements

## Quick Start

Register fitness checks using the `@runpod.serverless.register_fitness_check` decorator:

```python
import runpod
import torch

@runpod.serverless.register_fitness_check
def check_gpu():
    """Verify GPU is available."""
    if not torch.cuda.is_available():
        raise RuntimeError("GPU not available")

@runpod.serverless.register_fitness_check
def check_disk_space():
    """Verify sufficient disk space."""
    import shutil
    stat = shutil.disk_usage("/")
    free_gb = stat.free / (1024**3)
    if free_gb < 10:
        raise RuntimeError(f"Insufficient disk space: {free_gb:.2f}GB free")

def handler(job):
    """Your job handler."""
    return {"output": "success"}

if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
```

## Async Fitness Checks

Fitness checks support both synchronous and asynchronous functions:

```python
import runpod
import aiohttp

@runpod.serverless.register_fitness_check
async def check_api_connectivity():
    """Check if external API is accessible."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get("https://api.example.com/health", timeout=5) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"API health check failed: {resp.status}")
        except Exception as e:
            raise RuntimeError(f"Cannot connect to API: {e}")

def handler(job):
    return {"output": "success"}

if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
```

## Common Checks

### GPU Availability

```python
import runpod
import torch

@runpod.serverless.register_fitness_check
def check_gpu_available():
    """Verify GPU is available and has sufficient memory."""
    if not torch.cuda.is_available():
        raise RuntimeError("GPU is not available")

    # Optional: check GPU memory
    gpu_memory_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
    if gpu_memory_gb < 8:
        raise RuntimeError(f"GPU memory insufficient: {gpu_memory_gb:.1f}GB (need at least 8GB)")
```

### Model Files

```python
import runpod
from pathlib import Path

@runpod.serverless.register_fitness_check
def check_model_files():
    """Verify required model files exist."""
    required_files = [
        Path("/models/model.safetensors"),
        Path("/models/config.json"),
        Path("/models/tokenizer.model"),
    ]

    for file_path in required_files:
        if not file_path.exists():
            raise RuntimeError(f"Required file not found: {file_path}")
```

### Async Model Loading

```python
import runpod
import aiofiles.os

@runpod.serverless.register_fitness_check
async def check_models_loadable():
    """Verify models can be loaded (async)."""
    import torch

    try:
        # Test load model
        model = torch.load("/models/checkpoint.pt")
        del model  # Free memory
    except Exception as e:
        raise RuntimeError(f"Failed to load model: {e}")
```

### Disk Space

```python
import runpod
import shutil

@runpod.serverless.register_fitness_check
def check_disk_space():
    """Verify sufficient disk space for operations."""
    stat = shutil.disk_usage("/")
    free_gb = stat.free / (1024**3)
    required_gb = 50  # Adjust based on your needs

    if free_gb < required_gb:
        raise RuntimeError(
            f"Insufficient disk space: {free_gb:.2f}GB free, "
            f"need at least {required_gb}GB"
        )
```

### Environment Variables

```python
import runpod
import os

@runpod.serverless.register_fitness_check
def check_environment():
    """Verify required environment variables are set."""
    required_vars = ["API_KEY", "MODEL_PATH", "CONFIG_URL"]
    missing = [var for var in required_vars if not os.environ.get(var)]

    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")
```

## Behavior

### Execution Timing

- Fitness checks run **only once at worker startup**
- They run **before the first job is processed**
- They run **only on the actual RunPod serverless platform**
- Local development and testing modes skip fitness checks

### Execution Order

Fitness checks execute in the order they were registered (top to bottom in your code):

```python
import runpod

@runpod.serverless.register_fitness_check
def check_first():
    print("This runs first")

@runpod.serverless.register_fitness_check
def check_second():
    print("This runs second")
```

### Failure Behavior

If any fitness check fails:
1. An error is logged with the check name and exception details
2. The worker exits immediately with code 1
3. The container is marked as unhealthy
4. Your orchestrator (Kubernetes, Docker, etc.) can restart it

Example log output on failure:

```
ERROR  | Fitness check failed: check_gpu | RuntimeError: GPU not available
ERROR  | Worker is unhealthy, exiting.
```

### Success Behavior

If all checks pass:
1. A success message is logged
2. The worker continues startup normally
3. The heartbeat process starts
4. The worker begins accepting jobs

Example log output on success:

```
INFO   | Running 2 fitness check(s)...
DEBUG  | Executing fitness check: check_gpu
DEBUG  | Fitness check passed: check_gpu
DEBUG  | Executing fitness check: check_disk_space
DEBUG  | Fitness check passed: check_disk_space
INFO   | All fitness checks passed.
```

## Best Practices

### Keep Checks Fast

Minimize startup time by keeping checks simple and fast:

```python
# Good: Quick checks
@runpod.serverless.register_fitness_check
def check_gpu():
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("GPU not available")

# Avoid: Time-consuming operations
@runpod.serverless.register_fitness_check
def slow_check():
    import torch
    # Don't: Train a model or process large data
    model.train()  # This is too slow!
```

### Use Descriptive Error Messages

Clear error messages help with debugging:

```python
# Good: Specific error message
@runpod.serverless.register_fitness_check
def check_api():
    status = check_external_api()
    if status != 200:
        raise RuntimeError(
            f"External API returned status {status}, "
            f"expected 200. Check API_URL={os.environ.get('API_URL')}"
        )

# Avoid: Vague error message
@runpod.serverless.register_fitness_check
def bad_check():
    if not check_api():
        raise RuntimeError("API check failed")  # Not helpful
```

### Group Related Checks

Organize checks logically:

```python
# GPU checks
@runpod.serverless.register_fitness_check
def check_gpu_available():
    # ...

@runpod.serverless.register_fitness_check
def check_gpu_memory():
    # ...

# Model checks
@runpod.serverless.register_fitness_check
def check_model_files():
    # ...

@runpod.serverless.register_fitness_check
def check_model_loadable():
    # ...
```

### Handle Transient Failures Gracefully

For checks that might temporarily fail, consider retry logic:

```python
import runpod
import aiohttp
import asyncio

@runpod.serverless.register_fitness_check
async def check_api_with_retry():
    """Check API connectivity with retries."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://api.example.com/health", timeout=5) as resp:
                    if resp.status == 200:
                        return
        except Exception as e:
            if attempt == max_retries - 1:
                raise RuntimeError(f"API check failed after {max_retries} attempts: {e}")
            await asyncio.sleep(1)  # Wait before retry
```

## Testing

When developing locally, fitness checks don't run. To test them, you can manually invoke the runner:

```python
import asyncio
from runpod.serverless.modules.rp_fitness import run_fitness_checks, clear_fitness_checks

async def test_fitness_checks():
    """Test fitness checks manually."""
    try:
        await run_fitness_checks()
        print("All checks passed!")
    except SystemExit as e:
        print(f"Check failed with exit code: {e.code}")
    finally:
        clear_fitness_checks()

if __name__ == "__main__":
    asyncio.run(test_fitness_checks())
```

## Complete Example

Here's a complete example with multiple checks:

```python
import runpod
import os
import torch
import shutil
from pathlib import Path
import aiohttp

# GPU checks
@runpod.serverless.register_fitness_check
def check_gpu():
    """Verify GPU is available."""
    if not torch.cuda.is_available():
        raise RuntimeError("GPU not available")

@runpod.serverless.register_fitness_check
def check_gpu_memory():
    """Verify GPU has sufficient memory."""
    gpu_memory = torch.cuda.get_device_properties(0).total_memory / (1024**3)
    if gpu_memory < 8:
        raise RuntimeError(f"GPU memory too low: {gpu_memory:.1f}GB (need 8GB)")

# File checks
@runpod.serverless.register_fitness_check
def check_models_exist():
    """Verify model files exist."""
    model_path = Path("/models/model.safetensors")
    if not model_path.exists():
        raise RuntimeError(f"Model not found: {model_path}")

# Resource checks
@runpod.serverless.register_fitness_check
def check_disk_space():
    """Verify sufficient disk space."""
    stat = shutil.disk_usage("/")
    free_gb = stat.free / (1024**3)
    if free_gb < 50:
        raise RuntimeError(f"Insufficient disk space: {free_gb:.1f}GB free")

# Environment checks
@runpod.serverless.register_fitness_check
def check_environment():
    """Verify environment variables."""
    required = ["API_KEY", "MODEL_ID"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        raise RuntimeError(f"Missing env vars: {', '.join(missing)}")

# Async API check
@runpod.serverless.register_fitness_check
async def check_api():
    """Verify API is reachable."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.example.com/health", timeout=5) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"API returned {resp.status}")
    except Exception as e:
        raise RuntimeError(f"Cannot reach API: {e}")

def handler(job):
    """Process job."""
    job_input = job["input"]
    # Your processing code here
    return {"output": "success"}

if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
```

## Troubleshooting

### Checks Aren't Running

Fitness checks only run on the actual RunPod serverless platform, not locally. To debug locally:

```python
# Manually test your fitness checks
import asyncio
from runpod.serverless.modules.rp_fitness import run_fitness_checks

async def test():
    await run_fitness_checks()

asyncio.run(test())
```

### Worker Still Has Issues After Checks Pass

Fitness checks validate startup conditions. If issues occur during job processing, they won't be caught by fitness checks. Consider:
- Adding health checks in your handler
- Using try/catch in your job processing
- Logging detailed errors for debugging

### Performance Impact

Fitness checks add minimal overhead:
- Framework overhead: ~0.5ms per check
- Total for empty registry: ~0.1ms
- Typical total impact: 10-500ms depending on your checks

Keep checks fast to minimize startup time.

## See Also

- [Worker Basics](./README.md)
- [Async Handlers](./async_handlers.md)
- [Error Handling](./error_handling.md)
