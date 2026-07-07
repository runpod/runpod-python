"""the smallest possible app: one queue function, one remote call.

    rp dev examples/apps/01_hello_world.py --once
"""

import runpod
from runpod import App

app = App("ex-hello")


@app.queue(cpu="cpu3c-1-2")
def hello(name: str):
    print(f"saying hello to {name}")
    return f"hello {name}"


@runpod.local_entrypoint
def main():
    result = hello.remote("world")
    print("result:", result)
    assert result == "hello world"
