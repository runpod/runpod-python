#!/bin/bash
# Compile gpu_test binary for Linux x86_64 with CUDA support
# Usage: ./compile_gpu_test.sh
# Output: ../runpod/serverless/binaries/gpu_test

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/../runpod/serverless/binaries"
CUDA_VERSION="${CUDA_VERSION:-11.8.0}"
UBUNTU_VERSION="${UBUNTU_VERSION:-ubuntu22.04}"

# Create output directory if it doesn't exist
mkdir -p "$OUTPUT_DIR"

echo "Compiling gpu_test binary..."
echo "CUDA Version: $CUDA_VERSION"
echo "Ubuntu Version: $UBUNTU_VERSION"
echo "Output directory: $OUTPUT_DIR"

# Build in Docker container with NVIDIA CUDA development environment
docker run --rm \
  -v "$SCRIPT_DIR:/workspace" \
  "nvidia/cuda:${CUDA_VERSION}-devel-${UBUNTU_VERSION}" \
  bash -c "
    cd /workspace && \
    nvcc -O3 \
      -arch=sm_70 \
      -gencode=arch=compute_70,code=sm_70 \
      -gencode=arch=compute_75,code=sm_75 \
      -gencode=arch=compute_80,code=sm_80 \
      -gencode=arch=compute_86,code=sm_86 \
      -o gpu_test \
      gpu_test.c -lnvidia-ml -lcudart_static && \
    echo 'Compilation successful' && \
    file gpu_test
  "

# Move binary to output directory
if [ -f "$SCRIPT_DIR/gpu_test" ]; then
  mv "$SCRIPT_DIR/gpu_test" "$OUTPUT_DIR/gpu_test"
  chmod +x "$OUTPUT_DIR/gpu_test"
  echo "Binary successfully created at: $OUTPUT_DIR/gpu_test"
  echo "Binary info:"
  file "$OUTPUT_DIR/gpu_test"
else
  echo "Error: Compilation failed, binary not found"
  exit 1
fi
