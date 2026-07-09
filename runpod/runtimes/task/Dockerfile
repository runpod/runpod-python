# task runner image. BASE_IMAGE is python:X.Y-slim for cpu or
# runpod/gpu-base:pyX.Y-* for gpu (adds the torch family).
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# dependency layer: cached until requirements.txt changes, so code-only
# edits skip the full dependency install
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt cloudpickle \
 && rm /tmp/requirements.txt

# install the runpod package: task payloads carry the user's full module
# source, which imports runpod (decorators, nested .remote calls).
# the context has no .git, so setuptools-scm needs a pretend version
COPY . /src
RUN SETUPTOOLS_SCM_PRETEND_VERSION_FOR_RUNPOD=0.0.0.dev0 \
    pip install --no-cache-dir --no-deps /src && rm -rf /src

EXPOSE 8080
CMD ["python", "-m", "runpod.runtimes.task.runner"]
