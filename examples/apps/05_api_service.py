"""a load-balanced http service from a class with route markers.

    rp dev examples/apps/05_api_service.py --once
"""

import runpod
from runpod import App, get, init, post

app = App("ex-api")


@app.api(cpu="cpu3c-1-2")
class Counter:
    @init
    def setup(self):
        # runs once per worker before it takes traffic
        self.count = 0
        print("counter initialized")

    @post("/bump")
    async def bump(self, body: dict):
        self.count += body.get("by", 1)
        return {"count": self.count}

    @get("/value")
    async def value(self):
        return {"count": self.count}


@runpod.local_entrypoint
def main():
    first = Counter.post("/bump", {"by": 3})
    print("bump:", first)
    assert first["count"] == 3

    second = Counter.post("/bump", {"by": 2})
    assert second["count"] == 5

    value = Counter.get("/value")
    print("value:", value)
    assert value["count"] == 5
