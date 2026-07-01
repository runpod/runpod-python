"""manual e2e smoke test for the apps live path.

provisions a real cpu dev endpoint, executes a function remotely via
the live source-per-request protocol, and tears the endpoint down.
requires credentials in ~/.runpod/config.toml or RUNPOD_API_KEY.

run directly, not via pytest:
    python tests/e2e/apps_live_smoke.py
"""

import asyncio
import os
import sys

os.environ["RUNPOD_DEV_SESSION"] = "1"

from runpod.apps import App  # noqa: E402
from runpod.apps.dev import DevSession  # noqa: E402

app = App("apps-e2e-v2")


@app.queue(name="smoke", cpu="cpu3c-1-2", workers=(0, 1))
def smoke(x: int, y: int):
    return {"sum": x + y, "python": True}


async def main() -> int:
    session = DevSession([app])
    print("provisioning dev endpoint ...")
    await session.start()
    try:
        print("invoking smoke.remote(2, 3) ...")
        result = await smoke.remote.aio(2, 3)
        print(f"result: {result}")
        assert result == {"sum": 5, "python": True}, f"unexpected result: {result}"
        print("PASS")
        return 0
    finally:
        print("cleaning up ...")
        await session.stop()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
