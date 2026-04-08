# GPU Test Binary Compilation

This document explains how to rebuild the `gpu_test` binary for GPU health checking.

## When to Rebuild

You typically **do not need to rebuild** the binary. A pre-compiled version is included in the runpod-python package and works across most GPU environments. Rebuild only when:

- You need to modify the GPU test logic (in `build_tools/gpu_test.c`)
- Targeting specific new CUDA versions
- Adding support for new GPU architectures
- Fixing compilation issues for your specific environment

## Prerequisites

You need Docker installed to build the binary:

```bash
# Check Docker is available
docker --version
```

The build uses NVIDIA's official CUDA Docker image with development tools included.

## Building the Binary

### Basic Build

From the repository root:

```bash
# Navigate to build tools directory
cd build_tools

# Run the build script
./compile_gpu_test.sh

# Output created at: ../runpod/serverless/binaries/gpu_test
```

### Custom CUDA Version

To target a different CUDA version:

```bash
cd build_tools

# Build with CUDA 12.1
CUDA_VERSION=12.1.0 ./compile_gpu_test.sh

# Default is CUDA 11.8.0
CUDA_VERSION=11.8.0 ./compile_gpu_test.sh
```

### Custom Ubuntu Version

For different Ubuntu base images:

```bash
cd build_tools

# Build with Ubuntu 20.04 (wider compatibility)
UBUNTU_VERSION=ubuntu20.04 ./compile_gpu_test.sh

# Default is Ubuntu 22.04
UBUNTU_VERSION=ubuntu22.04 ./compile_gpu_test.sh
```

### Build Output

Successful compilation shows:

```
Compiling gpu_test binary...
CUDA Version: 11.8.0
Ubuntu Version: ubuntu22.04
Output directory: .../runpod/serverless/binaries
Compilation successful
Binary successfully created at: .../runpod/serverless/binaries/gpu_test
Binary info:
/path/to/gpu_test: ELF 64-bit LSB executable, x86-64, version 1 (SYSV), ...
```

## Testing the Binary

### Test on GPU Machine

If you have a GPU available:

```bash
# Run the compiled binary
./runpod/serverless/binaries/gpu_test

# Expected output:
# Linux Kernel Version: 5.15.0
# CUDA Driver Version: 12.2
# Found X GPUs:
# GPU 0: [GPU Name] (UUID: ...)
# GPU 0 memory allocation test passed.
# ...
```

### Verify Binary Properties

```bash
# Check binary info
file runpod/serverless/binaries/gpu_test

# Check binary size
ls -lh runpod/serverless/binaries/gpu_test

# Verify executable
test -x runpod/serverless/binaries/gpu_test && echo "Binary is executable"
```

## Compilation Details

### Source Code

Located at: `build_tools/gpu_test.c`

The binary:
- Uses NVIDIA CUDA Runtime API for GPU memory allocation testing
- Uses NVIDIA Management Library (NVML) for GPU enumeration
- Statically links CUDA runtime (no external CUDA runtime dependency)
- Dynamically links NVML (provided by NVIDIA driver)

### Target Architectures

The binary supports these GPU compute capabilities:

- **sm_70**: V100 (Volta), Titan V
- **sm_75**: RTX 2080, T4, RTX 2070, GTX 1660 Ti (Turing)
- **sm_80**: A100 (Ampere)
- **sm_86**: RTX 3090, RTX 3080, RTX 3070 (Ada)

This covers 99% of GPU workloads. To add support for newer architectures (sm_90 for H100/L40S):

```bash
# Edit build_tools/compile_gpu_test.sh and update the nvcc command:
nvcc -O3 \
  -arch=sm_70 \
  -gencode=arch=compute_70,code=sm_70 \
  ... (existing architectures)
  -gencode=arch=compute_90,code=sm_90 \  # Add for H100/L40S
  -o gpu_test \
  gpu_test.c -lnvidia-ml -lcudart_static
```

### Static vs Dynamic Linking

**CUDA Runtime**: Statically linked (`-lcudart_static`)
- Reason: CUDA runtime is large and varies with CUDA version
- Benefit: Binary works across different CUDA driver versions

**NVML**: Dynamically linked (`-lnvidia-ml`)
- Reason: NVML is always provided by the GPU driver
- Benefit: Avoids binary size inflation

## Troubleshooting

### "version mismatch" Error

The CUDA driver is too old for the compiled binary:

```bash
# Check CUDA driver version
nvidia-smi

# Recompile with an older CUDA version
CUDA_VERSION=11.2.0 ./compile_gpu_test.sh
```

### "symbol not found" Error

The compiled binary's glibc version is newer than the target system:

```bash
# Recompile with older Ubuntu base for better compatibility
UBUNTU_VERSION=ubuntu20.04 ./compile_gpu_test.sh
```

### "cannot execute binary" Error

Binary is corrupted or for wrong architecture:

```bash
# Verify binary integrity
file runpod/serverless/binaries/gpu_test

# Should show: ELF 64-bit LSB executable, x86-64

# Try recompiling
cd build_tools && ./compile_gpu_test.sh
```

### Build Fails: "nvcc not found"

Docker container missing CUDA development tools:

```bash
# Ensure Docker image includes dev tools
# Default image (nvidia/cuda:11.8.0-devel-ubuntu22.04) includes nvcc
# Try specifying full image with devel tag:
CUDA_VERSION=11.8.0 ./compile_gpu_test.sh
```

### Docker Permission Denied

You don't have permission to run Docker:

```bash
# Add current user to docker group
sudo usermod -aG docker $USER
newgrp docker

# Or use sudo
sudo ./compile_gpu_test.sh
```

## Deployment

### In Dockerfile

Include the binary in your container:

```dockerfile
# Copy pre-compiled binary from runpod-python
COPY runpod/serverless/binaries/gpu_test /usr/local/bin/

# Or compile in container
COPY build_tools/gpu_test.c /tmp/
RUN cd /tmp && nvcc -O3 -arch=sm_70,sm_75,sm_80,sm_86 \
    -o /usr/local/bin/gpu_test gpu_test.c -lnvidia-ml -lcudart_static
```

### Binary Size

Typical compiled binary size: 50-100 KB

This is negligible compared to typical container sizes.

## Version Compatibility

The compiled binary is compatible with:

| Component | Requirement |
|-----------|------------|
| OS | Linux x86_64 |
| glibc | 2.31+ (Ubuntu 20.04+) |
| CUDA Driver | 11.0+ |
| GPU Drivers | All modern NVIDIA drivers |

## See Also

- [Worker Fitness Checks](./worker_fitness_checks.md) - How GPU check is used
- [gpu_test.c source code](../../build_tools/gpu_test.c)
- [NVIDIA CUDA Documentation](https://docs.nvidia.com/cuda/)
- [NVIDIA NVML Documentation](https://docs.nvidia.com/deploy/nvml-api/)
