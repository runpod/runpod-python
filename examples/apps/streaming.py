"""streaming: yield partial results from a queue function.

A generator function decorated with @app.queue streams its output.
Calling .stream() yields each chunk as the worker produces it;
calling .remote() returns the full list of chunks at once. A spawned
job can be streamed later with job.stream().

Try it:

    rp dev examples/apps/streaming.py
"""

import time

import runpod

app = runpod.App("streaming")


@app.queue(cpu="cpu3c-1-2")
def tokens(prompt: str):
    # stand-in for token-by-token llm output
    for word in f"you said: {prompt}".split():
        time.sleep(0.2)
        yield word


@runpod.local_entrypoint
def main():
    # chunks arrive as the worker yields them
    for chunk in tokens.stream("hello streaming world"):
        print(chunk, end=" ", flush=True)
    print()

    # .remote() on a generator returns every chunk at once
    all_chunks = tokens.remote("all at once")
    print(all_chunks)

    # spawn now, stream later
    job = tokens.spawn("stream me later")
    print(list(job.stream()))
