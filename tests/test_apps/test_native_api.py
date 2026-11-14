"""native API declaration contracts."""

import sys
import typing
from functools import wraps

import pytest

from runpod import App
from runpod.apps.app import _clear_registry
from runpod.apps.errors import InvalidResourceError
from runpod.apps.markers import delete, get, patch, post, put


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


@pytest.mark.parametrize("marker", [get, post, put, patch, delete])
@pytest.mark.parametrize("path", ["/items/{item_id}", "/files/{path:path}"])
def test_path_parameters_rejected_at_marker(marker, path):
    with pytest.raises(ValueError, match="FastAPI factory"):
        marker(path)


@pytest.mark.parametrize("marker", [get, post, put, patch, delete])
@pytest.mark.parametrize("async_method", [False, True])
def test_no_argument_routes(marker, async_method):
    async def asynchronous(self):
        return {"ok": True}

    def synchronous(self):
        return {"ok": True}

    method = asynchronous if async_method else synchronous
    cls = type("Service", (), {"route": marker("/ok")(method)})
    app = App("test")
    handle = app.api()(cls)
    assert handle.spec.routes[0].handler_name == "route"
    assert app.resources["Service"] is handle


@pytest.mark.parametrize("marker", [post, put, patch, delete])
@pytest.mark.parametrize(
    "signature",
    [
        "self, payload",
        "self, payload: dict",
        "self, payload: typing.Dict",
        "self, payload: typing.Any",
        "self, payload: dict[str, typing.Any]",
        "self, payload: typing.Dict[str, typing.Any]",
        "self, payload: Dict[str, Any]",
        "self, payload: dict[str, Any]",
        "self, payload: 'dict'",
        "self, payload: 'typing.Dict'",
        "self, payload: 'typing.Any'",
        "self, payload: 'Dict'",
        "self, payload: 'Any'",
        "self, payload: 'dict[str, Any]'",
        "self, payload: 'typing.Dict[str, typing.Any]'",
        "self, payload: dict, /",
    ],
)
@pytest.mark.parametrize("postponed", [False, True])
def test_single_json_object_argument(marker, signature, postponed):
    namespace = {"typing": typing}
    source = "from __future__ import annotations\n" if postponed else ""
    source += "from typing import Any, Dict\n"
    source += f"async def route({signature}) -> 'Result':\n    return payload\n"
    exec(source, namespace)
    cls = type("Service", (), {"route": marker("/echo")(namespace["route"])})
    handle = App("test").api()(cls)
    assert handle.spec.routes[0].handler_name == "route"


@pytest.mark.parametrize(
    "annotation",
    [
        "t.Dict",
        "t.Any",
        "t.Dict[str, t.Any]",
        "dict[str, t.Any]",
        "Object",
        "Object[str, Value]",
        "Value",
        "JSONBody",
        "'JSONBody'",
    ],
)
@pytest.mark.parametrize("postponed", [False, True])
@pytest.mark.parametrize("wrapped", [False, True])
def test_imported_body_aliases(annotation, postponed, wrapped):
    namespace = {}
    source = "from __future__ import annotations\n" if postponed else ""
    source += (
        "import typing as t\n"
        "from typing import Dict as Object, Any as Value\n"
        "JSONBody = Object[str, Value]\n"
        f"def route(self, payload: {annotation}) -> 'UnresolvedReturn':\n"
        "    return payload\n"
    )
    exec(source, namespace)
    route = namespace["route"]
    if wrapped:
        original = route

        @wraps(original)
        def route(*args, **kwargs):
            return original(*args, **kwargs)

    cls = type("Service", (), {"route": post("/echo")(route)})
    assert App("test").api()(cls).spec.routes[0].handler_name == "route"


@pytest.mark.parametrize(
    "annotation",
    [
        "t.Dict[str, int]",
        "dict[str, int]",
        "t.Dict[int, t.Any]",
        "dict[int, t.Any]",
        "dict[t.Any, t.Any]",
        "Object[str, int]",
        "Schema",
        "Model",
        "Dict",
        "Any",
        "MissingBody",
        "t.MissingBody",
        "t.Annotated[dict[str, t.Any], 'body']",
    ],
)
@pytest.mark.parametrize("postponed", [False, True])
@pytest.mark.parametrize("wrapped", [False, True])
def test_invalid_body_aliases_identify_argument_and_annotation(
    annotation, postponed, wrapped
):
    namespace = {}
    source = "from __future__ import annotations\n" if postponed else ""
    source += (
        "import typing as t\n"
        "from typing import Dict as Object\n"
        "from pydantic import BaseModel\n"
        "class Schema(t.TypedDict):\n"
        "    count: int\n"
        "class Model(BaseModel):\n"
        "    count: int\n"
        "Dict = dict[str, int]\n"
        "Any = int\n"
        f"def route(self, payload: {annotation!r}) -> 'UnresolvedReturn':\n"
        "    return payload\n"
    )
    exec(source, namespace)
    route = namespace["route"]
    if wrapped:
        original = route

        @wraps(original)
        def route(*args, **kwargs):
            return original(*args, **kwargs)

    cls = type("Service", (), {"route": post("/echo")(route)})
    app = App("test")
    with pytest.raises(InvalidResourceError) as exc:
        app.api()(cls)
    message = str(exc.value)
    assert "body argument 'payload'" in message
    assert annotation in message
    assert "POST /echo (Service.route)" in message
    assert "FastAPI factory" in message
    assert app.resources == {}


@pytest.mark.parametrize(
    "signature", ["self, q", "self, q: int = 10", "self, body: dict"]
)
def test_get_arguments_rejected_before_registration(signature):
    namespace = {}
    exec(f"def route({signature}):\n    pass\n", namespace)
    cls = type("Service", (), {"route": get("/search")(namespace["route"])})
    app = App("test")
    with pytest.raises(InvalidResourceError, match=r"GET /search \(Service.route\)"):
        app.api()(cls)
    assert app.resources == {}


@pytest.mark.parametrize("marker", [post, put, patch, delete])
@pytest.mark.parametrize(
    "signature",
    [
        "",
        "*, self",
        "self, prompt, count",
        "self, prompt: str, count: int = 1",
        "self, body=None",
        "self, body: dict = {}",
        "self, *, body",
        "self, *args",
        "self, **kwargs",
        "self, body, *args",
        "self, body, **kwargs",
        "self, value: int",
        "self, value: str",
        "self, value: list",
        "self, value: dict[str, int]",
        "self, value: typing.Dict[int, typing.Any]",
        "self, value: dict[int, typing.Any]",
        "self, value: typing.Optional[dict]",
        "self, value: typing.Annotated[dict, 'body']",
        "self, value: 'RequestModel'",
        "self, value: 'typing.Dict[str, int]'",
    ],
)
def test_unsupported_body_signatures_rejected_before_registration(marker, signature):
    namespace = {"typing": typing}
    exec(f"def route({signature}):\n    pass\n", namespace)
    cls = type("Service", (), {"route": marker("/generate")(namespace["route"])})
    app = App("test")
    with pytest.raises(InvalidResourceError, match="FastAPI factory"):
        app.api()(cls)
    assert app.resources == {}


@pytest.mark.parametrize("descriptor", [staticmethod, classmethod])
@pytest.mark.parametrize("marker_outermost", [False, True])
def test_non_instance_methods_rejected(descriptor, marker_outermost):
    def route(self):
        return {"ok": True}

    member = (
        get("/ok")(descriptor(route))
        if marker_outermost
        else descriptor(get("/ok")(route))
    )
    cls = type("Service", (), {"route": member})
    app = App("test")
    with pytest.raises(InvalidResourceError, match="instance method"):
        app.api()(cls)
    assert app.resources == {}


def test_inherited_native_routes_are_validated():
    class Base:
        @get("/ok")
        def route(self):
            return {"ok": True}

    class Service(Base):
        pass

    app = App("test")
    assert app.api()(Service).spec.routes[0].handler_name == "route"

    class InvalidBase:
        @get("/search")
        def search(self, q="default"):
            return q

    class InvalidService(InvalidBase):
        pass

    with pytest.raises(InvalidResourceError, match="InvalidService.search"):
        app.api()(InvalidService)
    assert "InvalidService" not in app.resources


def test_typed_body_classes_require_factory():
    from pydantic import BaseModel

    class RequestModel(BaseModel):
        prompt: str

    class RequestDict(typing.TypedDict):
        prompt: str

    for annotation in (RequestModel, RequestDict):

        def route(self, body):
            return body

        route.__annotations__["body"] = annotation
        cls = type("Service", (), {"route": post("/generate")(route)})
        with pytest.raises(InvalidResourceError, match="body schemas"):
            App("test").api()(cls)


@pytest.mark.skipif(sys.version_info < (3, 14), reason="requires deferred annotations")
def test_deferred_body_aliases_capture_enclosing_scope():
    namespace = {}
    exec(
        "def make_route():\n"
        "    from typing import Dict as Object, Any as Value\n"
        "    def route(self, payload: Object[str, Value]) -> UnresolvedReturn:\n"
        "        return payload\n"
        "    return route\n",
        namespace,
    )
    cls = type("Service", (), {"route": post("/echo")(namespace["make_route"]())})
    assert App("test").api()(cls).spec.routes[0].handler_name == "route"


@pytest.mark.skipif(sys.version_info < (3, 14), reason="requires deferred annotations")
def test_deferred_body_aliases_with_unbound_return_closure():
    namespace = {"app": App("test"), "post": post}
    exec(
        "def declare():\n"
        "    from typing import Dict as Object, Any as Value\n"
        "    @app.api()\n"
        "    class Service:\n"
        "        @post('/echo')\n"
        "        def route(self, payload: Object[str, Value]) -> Service:\n"
        "            return payload\n"
        "    return Service\n",
        namespace,
    )
    assert namespace["declare"]().spec.routes[0].handler_name == "route"


@pytest.mark.parametrize("postponed", [False, True])
@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("signature", ["self", "self, payload: t.Dict[str, t.Any]"])
def test_return_annotations_are_not_evaluated(postponed, wrapped, signature):
    if not postponed and sys.version_info < (3, 14):
        pytest.skip("requires deferred annotations")
    namespace = {}
    source = "from __future__ import annotations\n" if postponed else ""
    source += (
        "import typing as t\n"
        "def invalid_return():\n"
        "    raise AssertionError('return annotation evaluated')\n"
        f"def route({signature}) -> invalid_return():\n"
        "    pass\n"
    )
    exec(source, namespace)
    route = namespace["route"]
    if wrapped:
        original = route

        @wraps(original)
        def route(*args, **kwargs):
            return original(*args, **kwargs)

    cls = type("Service", (), {"route": post("/echo")(route)})
    assert App("test").api()(cls).spec.routes[0].handler_name == "route"


@pytest.mark.skipif(sys.version_info < (3, 14), reason="requires deferred annotations")
def test_deferred_return_annotations_do_not_require_class_name_binding():
    app = App("test")
    namespace = {"app": app, "get": get}
    exec(
        "@app.api()\n"
        "class Service:\n"
        "    @get('/ok')\n"
        "    def route(self) -> Service:\n"
        "        return self\n",
        namespace,
    )
    assert namespace["Service"].spec.routes[0].handler_name == "route"


def test_factory_routes_are_not_native_declarations():
    from fastapi import Body, FastAPI
    from fastapi.testclient import TestClient

    app = App("test")

    @app.api()
    def web():
        server = FastAPI()

        @server.post("/items/{item_id}")
        def update(
            item_id: int,
            limit: int = 10,
            prompt: str = Body(...),
            count: int = Body(...),
        ):
            return {"id": item_id, "limit": limit, "prompt": prompt, "count": count}

        return server

    with TestClient(web._asgi_factory()) as client:
        response = client.post("/items/7?limit=2", json={"prompt": "hello", "count": 3})
        assert response.status_code == 200
        assert response.json() == {"id": 7, "limit": 2, "prompt": "hello", "count": 3}
        for path, body in (
            ("/items/bad", {"prompt": "hello", "count": 3}),
            ("/items/7?limit=bad", {"prompt": "hello", "count": 3}),
            ("/items/7", {"prompt": "hello"}),
            ("/items/7", {"prompt": "hello", "count": "bad"}),
        ):
            assert client.post(path, json=body).status_code == 422
