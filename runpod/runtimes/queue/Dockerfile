# queue worker image. BASE_IMAGE is python:X.Y-slim for cpu or
# runpod/gpu-base:pyX.Y-* for gpu (adds the torch family).
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC \
    PYTHONUNBUFFERED=1 \
    RUNPOD_RUNTIME_KIND=queue

RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# install the runpod package (worker loop + runtimes) from the build context
COPY . /src
RUN pip install --no-cache-dir /src cloudpickle && rm -rf /src

CMD ["python", "-m", "runpod.runtimes.bootstrap"]
