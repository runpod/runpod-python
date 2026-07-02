# task runner image. BASE_IMAGE is python:X.Y-slim for cpu or
# runpod/gpu-base:pyX.Y-* for gpu (adds the torch family).
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir cloudpickle

COPY runpod/runtimes/task/runner.py /runner.py

EXPOSE 8080
CMD ["python", "/runner.py"]
