# shared gpu base for the runtime images: python:X.Y-slim plus the
# torch family. this preinstalled set must stay in sync with
# SIZE_PROHIBITIVE_PACKAGES in runpod/apps/build.py: deploys targeting
# builtin gpu images exclude exactly these packages from artifacts and
# expect the image to provide them. torch cuda wheels bundle the cuda
# runtime; the host injects the driver.
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir torch torchvision torchaudio
