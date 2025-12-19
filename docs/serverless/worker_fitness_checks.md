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

### Automatic GPU Memory Allocation Test

GPU workers automatically run a built-in fitness check that validates GPU memory allocation. **No user action required** - this check runs automatically on GPU machines.

The check:
- Tests actual GPU memory allocation (cudaMalloc) to ensure GPUs are accessible
- Enumerates all detected GPUs and validates each one
- Uses a native CUDA binary for comprehensive testing
- Falls back to Python-based checks if the binary is unavailable
- Skips silently on CPU-only workers (allows same code for CPU/GPU)

```python
import runpod

# GPU health check runs automatically on GPU workers
# No manual registration needed!

def handler(job):
    """Your handler runs after GPU health check passes."""
    return {"output": "success"}

if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
```

**Configuration (Advanced)**:

You can customize the GPU check behavior with environment variables:

```python
import os

# Adjust timeout (default: 30 seconds)
os.environ["RUNPOD_GPU_TEST_TIMEOUT"] = "60"

# Override binary path (for custom/patched versions)
os.environ["RUNPOD_BINARY_GPU_TEST_PATH"] = "/custom/path/gpu_test"
```

**What it tests**:
- CUDA driver availability and version
- NVML initialization
- GPU enumeration
- Memory allocation capability for each GPU
- Actual GPU accessibility

**Success example**:
```
Linux Kernel Version: 5.15.0
CUDA Driver Version: 12.2
Found 2 GPUs:
GPU 0: NVIDIA A100 (UUID: GPU-xxx)
GPU 0 memory allocation test passed.
GPU 1: NVIDIA A100 (UUID: GPU-yyy)
GPU 1 memory allocation test passed.
```

**Failure handling**:
If the automatic GPU check fails, the worker exits immediately and is marked unhealthy. This ensures GPU workers only process jobs when GPUs are fully functional.

**Performance**:
- Execution time: 100-500ms per GPU (minimal startup impact)
- Covers V100, T4, A100, and RTX GPU families
- For detailed compilation information, see [GPU Binary Compilation Guide](./gpu_binary_compilation.md)

## Built-in System Checks

The following system resource checks run automatically on every worker startup. **No user action required** - these checks validate system readiness before accepting jobs.

**Check Summary:**
- **3 checks for all workers**: Memory, Disk Space, Network Connectivity
- **3 additional checks for GPU workers**: CUDA Version, CUDA Device Initialization, GPU Compute Benchmark
- **Total: 3-6 checks** depending on worker type

### Memory Availability

Ensures sufficient RAM is available for job execution.

- **Default**: 4GB minimum
- **Configure**: `RUNPOD_MIN_MEMORY_GB=8.0`

What it checks:
- Total system memory
- Available memory (accounting for caching/buffers)
- Memory usage percentage

Example log output:
```
Memory check passed: 12.00GB available (of 16.00GB total)
```

### Disk Space

Verifies adequate disk space on root filesystem and /tmp (common for model downloads).

Requires free space to be at least a percentage of total disk size, which automatically scales to different machine sizes.

- **Default**: 10% of total disk must be free
- **Configure**: `RUNPOD_MIN_DISK_PERCENT=15` (or any percentage 0-100)

What it checks:
- Root filesystem (/) free space percentage
- Temporary directory (/tmp) free space percentage
- Automatic scaling based on total disk size

Scaling examples with 10% default:
- 100GB disk: requires 10GB free
- 1TB disk: requires 100GB free
- 10TB disk: requires 1TB free

Example log output:
```
Disk space check passed on /: 50.00GB free (50.0% available)
```

### Network Connectivity

Tests basic internet connectivity for API calls and job processing.

- **Default**: 5 second timeout to 8.8.8.8:53
- **Configure**: `RUNPOD_NETWORK_CHECK_TIMEOUT=10`

What it checks:
- Connection to Google DNS (8.8.8.8 port 53)
- Response latency
- Overall internet accessibility

Example log output:
```
Network connectivity passed: Connected to 8.8.8.8 (45ms)
```

### CUDA Version (GPU workers only)

Validates CUDA driver version meets minimum requirements. Skips silently on CPU-only workers.

- **Default**: CUDA 11.8+
- **Configure**: `RUNPOD_MIN_CUDA_VERSION=12.0`

What it checks:
- CUDA driver version (via nvcc or nvidia-smi)
- Version compatibility
- GPU driver accessibility

Example log output:
```
CUDA version check passed: 12.2 (minimum: 11.8)
```

### CUDA Device Initialization (GPU workers only)

Verifies CUDA devices can be initialized and are accessible. This catches runtime failures where CUDA appears available but fails during actual use (out of memory, device busy, driver issues, etc.).

What it checks:
- CUDA device initialization succeeds
- Device count is correct
- Each device has accessible memory
- Tensor allocation works on all devices
- Device synchronization succeeds

This check runs AFTER the CUDA version check to catch initialization failures early at startup rather than during job processing.

Example log output:
```
CUDA initialization passed: 2 device(s) initialized successfully
```

**Failure scenario** (caught early):
```
ERROR  | Fitness check failed: _cuda_init_check | RuntimeError: Failed to initialize GPU 0: CUDA error: CUDA-capable device(s) is/are busy or unavailable
```

### GPU Compute Benchmark (GPU workers only)

Quick matrix multiplication to verify GPU compute functionality and responsiveness. Skips silently on CPU-only workers.

- **Default**: 100ms maximum execution time
- **Configure**: `RUNPOD_GPU_BENCHMARK_TIMEOUT=2`

What it tests:
- GPU compute capability (matrix multiplication)
- GPU response time
- Memory bandwidth to GPU

If the operation takes longer than 100ms, the worker exits as the GPU is too slow for reliable job processing.

Example log output:
```
GPU compute benchmark passed: Matrix multiply completed in 25ms
```

### Configuring Built-in Checks

All thresholds are configurable via environment variables. For example:

```dockerfile
# In your Dockerfile or container config
ENV RUNPOD_MIN_MEMORY_GB=8.0
ENV RUNPOD_MIN_DISK_PERCENT=15.0
ENV RUNPOD_MIN_CUDA_VERSION=12.0
ENV RUNPOD_NETWORK_CHECK_TIMEOUT=10
ENV RUNPOD_GPU_BENCHMARK_TIMEOUT=2
```

Or in Python:

```python
import os

os.environ["RUNPOD_MIN_MEMORY_GB"] = "8.0"
os.environ["RUNPOD_MIN_DISK_PERCENT"] = "15.0"
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
