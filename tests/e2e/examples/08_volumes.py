"""shared network volume: one task writes, another reads.

the volume is created on first use; placement co-locates both tasks
in the volume's datacenter automatically.

    rp dev tests/e2e/examples/08_volumes.py --once
"""

import runpod
from runpod import App, Volume

app = App("ex-volumes")

scratch = Volume("ex-scratch", size=10)


@app.task(cpu="cpu3c-1-2", volume=scratch)
def write(content: str):
    target = scratch.path / "message.txt"
    target.write_text(content)
    print(f"wrote {len(content)} bytes to {target}")
    return str(target)


@app.task(cpu="cpu3c-1-2", volume=scratch)
def read():
    target = scratch.path / "message.txt"
    content = target.read_text()
    print(f"read {len(content)} bytes from {target}")
    return content


@runpod.local_entrypoint
def main():
    message = "hello from the other pod"
    write.remote(message)
    result = read.remote()
    print("read back:", result)
    assert result == message
