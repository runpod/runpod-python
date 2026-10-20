"""tests for the App registry and decorators."""

from unittest.mock import patch

import pytest

import runpod
from runpod.apps import App, ApiHandle, FunctionHandle, ResourceKind
from runpod.apps.app import _REGISTRY, _clear_registry
from runpod.apps.errors import InvalidResourceError
from runpod.apps.gpu import live_pool_members as _real_live_pool_members
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


@pytest.mark.parametrize("cpu", [[], "", ["cpu3c-2-4", ""], [None]])
def test_cpu_selection_requires_nonempty_instance_ids(cpu):
    app = App("a")

    with pytest.raises(InvalidResourceError):
        app.task(cpu=cpu)(lambda: None)

    assert app.resources == {}


async def test_remote_rejects_cleared_cpu_selection(monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "runpod.apps.tasks.TaskExecution.start",
        AsyncMock(side_effect=AssertionError("invalid specs must not provision")),
    )
    app = App("a")

    @app.task(cpu="cpu3c-2-4")
    def task():
        raise AssertionError("invalid specs must not execute")

    task.spec.cpu.clear()
    with pytest.raises(InvalidResourceError):
        await task.remote.aio()


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


async def test_remote_rejects_mutated_invalid_spec():
    app = App("a")

    @app.queue(name="q")
    def q():
        raise AssertionError("invalid specs must not execute")

    q.spec.workers = (9, 1)
    with pytest.raises(InvalidResourceError):
        await q.remote.aio()


@pytest.mark.parametrize("decorator", ["queue", "task", "api"])
@pytest.mark.parametrize(
    "mounts",
    ["models", ["models"], {"/runpod-volume": "models"}],
)
def test_decorators_reject_implicit_storage_references(decorator, mounts):
    from runpod.apps.volume import VolumeError

    app = App("a")
    with pytest.raises(VolumeError):
        getattr(app, decorator)(mounts=mounts)(lambda: None)


def test_manifest_serialization():
    app = App("a")
    volume = runpod.NetworkVolume("my-volume")

    @app.queue(
        name="q",
        gpu=[runpod.GpuGroup.ADA_24],
        workers=(1, 3),
        dependencies=["torch"],
        mounts={"/runpod-volume": volume},
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
        "mounts": [
            {"kind": "network", "reference": "my-volume", "path": "/runpod-volume"}
        ],
        "env": {"KEY": "val"},
    }


def test_task_manifest_preserves_mount_backend_identity_and_path():
    app = App("a")

    @app.task(
        mounts={
            "/network": runpod.NetworkVolume("shared"),
            "/global": runpod.GlobalVolume("shared"),
        }
    )
    def work():
        pass

    assert work.spec.to_manifest()["mounts"] == [
        {"kind": "network", "reference": "shared", "path": "/network"},
        {"kind": "global", "reference": "shared", "path": "/global"},
    ]


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

    def test_gpu_ids_excludes_other_live_pool_members(self, monkeypatch):
        from runpod.apps import gpu as gpu_mod

        live = {
            "ADA_24": {"NVIDIA GeForce RTX 4090"},
            "AMPERE_24": {
                "NVIDIA GeForce RTX 3090",
                "NVIDIA L4",
                "NVIDIA RTX A5000",
                "NVIDIA RTX PRO 6000 Blackwell Server Edition MIG 1g.24gb",
            },
        }
        monkeypatch.setattr(gpu_mod, "live_pool_members", lambda: live)
        value = gpu_mod.gpu_ids_value(
            ["NVIDIA GeForce RTX 4090", "NVIDIA L4", "NVIDIA RTX A5000"]
        )
        assert value == (
            "ADA_24,AMPERE_24,-NVIDIA GeForce RTX 3090,"
            "-NVIDIA RTX PRO 6000 Blackwell Server Edition MIG 1g.24gb"
        )

    def test_gpu_ids_named_pool_allows_all_members(self, monkeypatch):
        from runpod.apps import gpu as gpu_mod

        monkeypatch.setattr(
            gpu_mod,
            "live_pool_members",
            lambda: {"AMPERE_24": {"NVIDIA L4", "NVIDIA GeForce RTX 3090"}},
        )
        assert gpu_mod.gpu_ids_value(["NVIDIA L4", "AMPERE_24"]) == "AMPERE_24"

    def test_gpu_ids_falls_back_to_static_pools(self, monkeypatch):
        from runpod.apps import gpu as gpu_mod

        monkeypatch.setattr(gpu_mod, "live_pool_members", lambda: None)
        assert gpu_mod.gpu_ids_value(["NVIDIA L4"]) == (
            "AMPERE_24,-NVIDIA GeForce RTX 3090,-NVIDIA RTX A5000"
        )

    def test_live_pool_members_parses_catalog_and_fails_soft(self, monkeypatch):
        from unittest.mock import MagicMock

        from runpod.apps import gpu as gpu_mod

        monkeypatch.setenv("RUNPOD_API_KEY", "k")
        resp = MagicMock()
        resp.json.return_value = {
            "gpus": [
                {"id": "NVIDIA L4", "pool": "AMPERE_24"},
                {"id": "NVIDIA A100-SXM4-40GB", "pool": None},
            ]
        }
        fetch = _real_live_pool_members
        gpu_mod._clear_catalog_cache()
        with patch("requests.get", return_value=resp) as get:
            assert fetch() == {"AMPERE_24": {"NVIDIA L4"}}
            assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer k"
        gpu_mod._clear_catalog_cache()
        with patch("requests.get", side_effect=OSError("offline")) as get:
            assert fetch() is None
            # a failure is not retried on every lookup
            assert fetch() is None
            assert get.call_count == 1
        # it is retried once the window passes
        cache = next(iter(gpu_mod._catalog_caches.values()))
        cache.failed_at -= gpu_mod.CATALOG_RETRY_SECONDS
        with patch("requests.get", return_value=resp):
            assert fetch() == {"AMPERE_24": {"NVIDIA L4"}}
        gpu_mod._clear_catalog_cache()

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
