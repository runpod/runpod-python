# Native API routes

`@app.api` accepts a class of native routes or a zero-argument factory returning
an ASGI application. Native routes receive either no input or a complete JSON
object. Use a FastAPI factory for typed path and query parameters, individual
body fields, or schema validation.

```python
from runpod import App, get, post

app = App("inference")

@app.api(cpu="cpu3c-1-2")
class Inference:
    @get("/health")
    def health(self):
        return {"ok": True}

    @post("/generate")
    async def generate(self, body: dict):
        return {"prompt": body.get("prompt")}
```

## Signature contract

Native routes must be ordinary instance methods. Synchronous and asynchronous
methods are supported, including inherited methods. Static methods and class
methods are unsupported.

| Marker | Parameters after the instance argument |
| --- | --- |
| `@get` | None |
| `@post`, `@put`, `@patch`, `@delete` | None, or one required positional argument |

The single argument can have any name and receives the entire JSON object, not
a field with that name. Its annotation must be absent, `dict`, `typing.Dict`, or
`typing.Any` (including their string forms). These annotations do not declare a
body schema. Parameterized dictionaries such as `dict[str, int]`, Pydantic
models, `TypedDict`, scalar types, and `Annotated` metadata are unsupported.
Native handlers must validate their own fields. Return annotations do not
validate responses.

Default values, keyword-only arguments, `*args`, and `**kwargs` are unsupported.
Paths must be literal, such as `/generate`, not templates such as `/items/{id}`.
Native methods do not bind query parameters. Unsupported declarations raise an
error while the application is imported, before the resource is registered.

Routes with a body argument require a JSON object. Missing bodies, malformed
JSON, `null`, arrays, and scalar values receive HTTP 422. Object fields are
passed through without coercion or field validation. Methods without a body
argument do not consume the request body. These request semantics apply to both
live development and deployed workers with the matching API runtime release.

## Typed input with FastAPI

An ASGI factory gives FastAPI ownership of parameter binding and validation.
Declare `fastapi` as a resource dependency so it is available on the worker.

```python
from runpod import App

app = App("typed-api")

@app.api(cpu="cpu3c-1-2", dependencies=["fastapi"])
def web():
    from fastapi import Body, FastAPI

    server = FastAPI()

    @server.post("/items/{item_id}")
    async def update(
        item_id: int,
        limit: int = 10,
        prompt: str = Body(...),
        count: int = Body(...),
    ):
        return {"id": item_id, "limit": limit, "prompt": prompt, "count": count}

    return server
```

`POST /items/7?limit=2` with `{"prompt": "hello", "count": 3}` binds all four
arguments. An invalid integer path or query value, or a missing or invalid body
field, receives HTTP 422 from FastAPI.
