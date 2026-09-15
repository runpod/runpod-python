"""manual e2e smoke test for @app.task pod execution.

deploys a real cpu pod via the env-injection bootstrap (python:3.12-slim
so the test does not depend on the runpod/task images existing yet),
executes a function, and terminates the pod.

run directly, not via pytest:
    python tests/e2e/task_pod_smoke.py
"""

import asyncio
import sys

from runpod.apps import App

app = App("task-e2e")


@app.task(name="smoke", cpu="cpu3c-1-2", image="python:3.12-slim")
def smoke(x: int, y: int):
    import platform

    return {"product": x * y, "python": platform.python_version()}


async def main() -> int:
    print("running smoke.remote(6, 7) (deploys pod, executes, terminates) ...")
    result = await smoke.remote.aio(6, 7)
    print(f"result: {result}")
    assert result["product"] == 42, f"unexpected result: {result}"
    assert result["python"].startswith("3.12")
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
