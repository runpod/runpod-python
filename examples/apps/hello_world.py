"""Your first app: a Python function that runs in the cloud.

An App is a named collection of functions. Decorating a function with
@app.queue turns it into a remote resource — calling .remote() sends
the call to a cloud worker and blocks until the result comes back.

Try it:

    rp dev examples/apps/hello_world.py

Edit the greeting while the session is running, then press enter to
re-run — the worker picks up your new code automatically.
"""

import runpod
from runpod import App

app = App("hello")


@app.queue(cpu="cpu3c-1-2")
def hello(name: str):
    # this print shows up in your terminal, streamed from the worker
    print(f"running in the cloud, greeting {name}")
    return f"hello {name}!"


@runpod.local_entrypoint
def main():
    # runs on your machine; hello() runs on a cloud worker
    greeting = hello.remote("world")
    print(greeting)
