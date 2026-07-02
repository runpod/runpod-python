# shared gpu base for the runtime images: python:X.Y-slim plus the
# torch family. this preinstalled set must stay in sync with
# SIZE_PROHIBITIVE_PACKAGES in runpod/apps/build.py: deploys targeting
# builtin gpu images exclude exactly these packages from artifacts and
# expect the image to provide them. torch cuda wheels bundle the cuda
# runtime; the host injects the driver.
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

# nvidia container runtime keys on these to inject the host driver;
# without them torch sees no gpu even on a gpu machine
ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC \
    PYTHONUNBUFFERED=1 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# cu128 wheels: default pypi torch is cu130, which needs 580+ drivers
# that much of the fleet does not have yet. cu128 runs on 12.8+ drivers.
# py3.10 needs an explicit pin: the newest packages on the cu128 index
# no longer resolve together for cp310.
RUN if python -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 10) else 1)'; then \
        pip install --no-cache-dir torch==2.9.1 torchvision==0.24.1 torchaudio==2.9.1 \
            --index-url https://download.pytorch.org/whl/cu128; \
    else \
        pip install --no-cache-dir torch torchvision torchaudio \
            --index-url https://download.pytorch.org/whl/cu128; \
    fi
