"""A load-balanced HTTP service from a plain class.

@app.api turns a class into a web service: mark methods with @get and
@post to expose routes, and @init to run setup once per worker before
it takes traffic. Workers keep state between requests — this counter
lives in memory on the worker.

    rp dev examples/apps/web_service.py

Deployed services get a stable URL; in dev you call routes through the
class itself, as shown in the entrypoint.
"""

import runpod

app = runpod.App("web")


@app.api(cpu="cpu3c-1-2")
class Counter:
    @runpod.init
    def setup(self):
        # runs once when a worker starts, before any request
        self.count = 0
        print("worker ready, counter at 0")

    @runpod.post("/bump")
    async def bump(self, body: dict):
        self.count += body.get("by", 1)
        return {"count": self.count}

    @runpod.get("/value")
    async def value(self):
        return {"count": self.count}


@runpod.local_entrypoint
def main():
    print(Counter.post("/bump", {"by": 3}))   # {'count': 3}
    print(Counter.post("/bump", {"by": 2}))   # {'count': 5}
    print(Counter.get("/value"))              # {'count': 5}
