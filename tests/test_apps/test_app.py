"""tests for the App registry and decorators."""

import pytest

import runpod
from runpod.apps import App, ApiHandle, FunctionHandle, ResourceKind
from runpod.apps.app import _REGISTRY, _clear_registry
from runpod.apps.errors import InvalidResourceError
from runpod.apps.markers import get, init, post


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


def test_app_registers_itself():
    app = App("my-app")
    assert app in _REGISTRY
    assert runpod.apps.get_registered_apps() == [app]


def test_app_requires_name():
    with pytest.raises(InvalidResourceError):
        App("")


def test_queue_decorator_returns_handle():
    app = App("a")

    @app.queue(name="q", gpu=runpod.GpuType.NVIDIA_L4, workers=3)
    async def q(x: int):
        return x

    assert isinstance(q, FunctionHandle)
    assert q.spec.kind is ResourceKind.QUEUE
    assert q.spec.name == "q"
    assert q.spec.gpu == ["NVIDIA L4"]
    assert q.spec.workers == (0, 3)
    assert app.resources["q"] is q


def test_queue_name_defaults_to_function_name():
    app = App("a")

    @app.queue()
    def my_fn():
        return 1

    assert my_fn.spec.name == "my_fn"


def test_task_decorator():
    app = App("a")

    @app.task(name="t", cpu="cpu5c-2-4")
    def t():
        return "ok"

    assert t.spec.kind is ResourceKind.TASK
    assert t.spec.cpu == ["cpu5c-2-4"]


def test_gpu_cpu_mutually_exclusive():
    app = App("a")
    with pytest.raises(InvalidResourceError):

        @app.queue(name="bad", gpu=runpod.GpuType.NVIDIA_L4, cpu="cpu5c-2-4")
        def bad():
            pass


def test_duplicate_resource_name_rejected():
    app = App("a")

    @app.queue(name="dupe")
    def one():
        pass

    with pytest.raises(InvalidResourceError):

        @app.queue(name="dupe")
        def two():
            pass


def test_workers_int_shorthand():
    app = App("a")

    @app.queue(name="q", workers=5)
    def q():
        pass

    assert q.spec.workers == (0, 5)


def test_workers_tuple():
    app = App("a")

    @app.queue(name="q", workers=(1, 4))
    def q():
        pass

    assert q.spec.workers == (1, 4)


def test_workers_invalid():
    app = App("a")
    with pytest.raises(InvalidResourceError):

        @app.queue(name="q", workers=(3, 1))
        def q():
            pass


def test_api_class_collects_routes():
    app = App("a")

    @app.api(name="api", cpu="cpu5c-2-4")
    class Inference:
        @init
        def setup(self):
            self.model = object()

        @get("/health")
        def health(self):
            return {"ok": True}

        @post("/generate")
        async def generate(self, body: dict):
            return body

    assert isinstance(Inference, ApiHandle)
    routes = {(r.method, r.path) for r in Inference.spec.routes}
    assert routes == {("GET", "/health"), ("POST", "/generate")}
    assert Inference._init_name == "setup"


def test_api_class_without_routes_rejected():
    app = App("a")
    with pytest.raises(InvalidResourceError):

        @app.api(name="api")
        class Empty:
            def nothing(self):
                pass


def test_api_duplicate_route_rejected():
    app = App("a")
    with pytest.raises(InvalidResourceError):

        @app.api(name="api")
        class Dupe:
            @post("/x")
            def one(self):
                pass

            @post("/x")
            def two(self):
                pass


def test_api_asgi_factory():
    app = App("a")

    @app.api(name="web", cpu="cpu5c-2-4")
    def web():
        return object()

    assert isinstance(web, ApiHandle)
    assert web.spec.asgi_factory is not None


def test_api_reserved_path_rejected():
    with pytest.raises(ValueError):
        post("/execute")


def test_schedule_below_app_decorator():
    app = App("a")

    @app.task(name="cron")
    @runpod.schedule(cron="0 * * * *")
    async def cron():
        pass

    assert cron.spec.schedule == "0 * * * *"


def test_schedule_above_app_decorator():
    app = App("a")

    @runpod.schedule(cron="*/5 * * * *")
    @app.task(name="cron")
    async def cron():
        pass

    assert cron.spec.schedule == "*/5 * * * *"


def test_handle_direct_call_raises():
    app = App("a")

    @app.queue(name="q")
    def q():
        pass

    with pytest.raises(TypeError, match=r"\.remote\("):
        q()


def test_handle_local_runs_body():
    app = App("a")

    @app.queue(name="q")
    def q(x):
        return x * 2

    assert q.local(21) == 42


async def test_handle_local_async_follows_signature():
    app = App("a")

    @app.queue(name="q")
    async def q(x):
        return x + 1

    assert await q.local(1) == 2


def test_manifest_serialization():
    app = App("a")
    volume = "my-volume"

    @app.queue(
        name="q",
        gpu=[runpod.GpuGroup.ADA_24],
        workers=(1, 3),
        dependencies=["torch"],
        volume=volume,
        env={"KEY": "val"},
    )
    def q():
        pass

    manifest = q.spec.to_manifest()
    assert manifest == {
        "kind": "queue",
        "name": "q",
        "gpuCount": 1,
        "workersMin": 1,
        "workersMax": 3,
        "idleTimeout": 60,
        "gpus": ["ADA_24"],
        "dependencies": ["torch"],
        "networkVolume": "my-volume",
        "env": {"KEY": "val"},
    }


class TestGpuStringResolution:
    def test_pool_id_passthrough(self):
        from runpod.apps.spec import normalize_gpu

        assert normalize_gpu("ADA_24") == ["ADA_24"]
        assert normalize_gpu("ada_24") == ["ADA_24"]

    def test_device_name_passthrough(self):
        from runpod.apps.spec import normalize_gpu

        assert normalize_gpu("NVIDIA B200") == ["NVIDIA B200"]

    def test_enum_style_name(self):
        from runpod.apps.spec import normalize_gpu

        assert normalize_gpu("NVIDIA_B200") == ["NVIDIA B200"]

    def test_shorthand_fragments(self):
        from runpod.apps.spec import normalize_gpu

        assert normalize_gpu("B200") == ["NVIDIA B200"]
        assert normalize_gpu("4090") == ["NVIDIA GeForce RTX 4090"]
        assert normalize_gpu("5090") == ["NVIDIA GeForce RTX 5090"]

    def test_fragment_matches_all_variants(self):
        from runpod.apps.spec import normalize_gpu

        matches = normalize_gpu("A100")
        assert set(matches) == {
            "NVIDIA A100 80GB PCIe",
            "NVIDIA A100-SXM4-80GB",
        }

    def test_any_sentinel(self):
        from runpod.apps.spec import normalize_gpu

        assert normalize_gpu("any") == ["any"]

    def test_unknown_raises_at_decoration(self):
        import pytest

        from runpod.apps.errors import InvalidResourceError
        from runpod.apps.spec import normalize_gpu

        with pytest.raises(InvalidResourceError, match="unknown gpu 'B300'"):
            normalize_gpu("B300")

    def test_gpu_ids_maps_devices_to_pools(self):
        from runpod.apps.gpu import gpu_ids_value

        # endpoints select by pool; device names map back
        assert gpu_ids_value(["NVIDIA B200"]) == "BLACKWELL_180"
        assert gpu_ids_value(["NVIDIA GeForce RTX 4090"]) == "ADA_24"
        # pool ids pass through untouched
        assert gpu_ids_value(["ADA_24"]) == "ADA_24"
        # duplicates collapse (two devices in the same pool)
        assert (
            gpu_ids_value(["NVIDIA A100 80GB PCIe", "NVIDIA A100-SXM4-80GB"])
            == "AMPERE_80"
        )
