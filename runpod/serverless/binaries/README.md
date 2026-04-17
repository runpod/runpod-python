# GPU Test Binary

Pre-compiled GPU health check binary for Linux x86_64.

## Files

- `gpu_test` - Compiled binary for CUDA GPU memory allocation testing (not
  bundled in the PyPI wheel; see below)

## Availability

As of runpod v1.10.0 this binary is **not included** in the PyPI wheel. The
universal `py3-none-any` wheel would otherwise advertise itself as
platform-agnostic while shipping a Linux x86_64 ELF, which breaks Nix and
other strict packagers (see [#498](https://github.com/runpod/runpod-python/issues/498)).

Runpod GPU workers can download the matching binary with:

```bash
runpod install-gpu-test
```

This fetches the asset from the GitHub release matching the installed runpod
version and verifies its sha256.

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

See `build_tools/compile_gpu_test.sh` and `docs/serverless/gpu_binary_compilation.md`.

## License

Same as runpod-python package (MIT License)
