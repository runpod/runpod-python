"""a custom container image: the sdk bootstraps its runtime onto any
image with python3.

    rp dev tests/e2e/examples/12_custom_image.py --once
"""

import runpod

app = runpod.App("ex-image")


@app.queue(cpu="cpu3c-1-2", image="python:3.12-slim")
def python_version():
    import sys

    return f"{sys.version_info.major}.{sys.version_info.minor}"


@runpod.local_entrypoint
def main():
    version = python_version.remote()
    print("worker python:", version)
    assert version == "3.12"


if __name__ == "__main__":
    main()
