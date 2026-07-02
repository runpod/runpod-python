ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir cloudpickle

COPY runpod/runtimes/task/runner.py /runner.py

EXPOSE 8080
CMD ["python", "/runner.py"]
