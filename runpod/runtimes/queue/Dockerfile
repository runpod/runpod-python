ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# install the runpod package (worker loop + runtimes) from the build context
COPY . /src
RUN pip install --no-cache-dir /src cloudpickle && rm -rf /src

CMD ["python", "-m", "runpod.runtimes.queue.bootstrap"]
