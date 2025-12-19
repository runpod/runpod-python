# GPU Test Binary

Pre-compiled GPU health check binary for Linux x86_64.

## Files

- `gpu_test` - Compiled binary for CUDA GPU memory allocation testing

## Compatibility

- **OS**: Linux x86_64 (glibc 2.31+)
- **CUDA**: 11.8+ driver
- **GPUs**: Volta (V100), Turing (T4), Ampere (A100), Ada (RTX 4090) architectures

## Usage

```bash
./runpod/serverless/binaries/gpu_test
```

**Output example**:
```
Linux Kernel Version: 5.15.0
CUDA Driver Version: 12.2
Found 1 GPUs:
GPU 0: NVIDIA A100 (UUID: GPU-xxx)
GPU 0 memory allocation test passed.
```

## Building

See `build_tools/compile_gpu_test.sh` and `docs/serverless/gpu_binary_compilation.md` for compilation instructions.

## License

Same as runpod-python package (MIT License)
