"""model references: parsing, paths, payload plumbing."""

from pathlib import Path

import pytest

from runpod.apps.model import Model, ModelError, model_reference


class TestModelRef:
    def test_repo_parse(self):
        model = Model("meta-llama/Llama-3.1-8B-Instruct")
        assert model.owner == "meta-llama"
        assert model.name == "Llama-3.1-8B-Instruct"
        assert model.revision is None

    def test_revision_parse(self):
        model = Model("org/name:abc123")
        assert model.revision == "abc123"
        assert model.reference == "org/name:abc123"

    def test_invalid_references(self):
        for bad in ("", "no-slash", "a/b/c", "spaces in/name"):
            with pytest.raises(ModelError):
                Model(bad)

    def test_store_path_with_env_revision(self, monkeypatch):
        monkeypatch.setenv("MODEL_REVISION", "deadbeef")
        model = Model("org/name")
        assert model.path == Path(
            "/runpod/model-store/huggingface/org/name/deadbeef"
        )

    def test_store_path_without_revision(self, monkeypatch):
        monkeypatch.delenv("MODEL_REVISION", raising=False)
        model = Model("org/name")
        assert model.path == Path("/runpod/model-store/huggingface/org/name")

    def test_hf_cache_path(self):
        model = Model("org/name")
        assert model.hf_cache_path == Path(
            "/runpod-volume/huggingface-cache/hub/models--org--name"
        )

    def test_model_reference_normalization(self):
        assert model_reference(None) is None
        assert model_reference("org/name") == "org/name"
        assert model_reference(Model("org/name:rev")) == "org/name:rev"
        with pytest.raises(ModelError):
            model_reference(42)


class TestSpecPlumbing:
    def test_decorator_accepts_model(self):
        from runpod.apps.app import App

        app = App("modeltest")
        llama = Model("meta-llama/Llama-3.1-8B-Instruct")

        @app.queue(name="chat", gpu="4090", model=llama)
        def chat(prompt: str):
            pass

        assert chat.spec.model is llama
        assert (
            chat.spec.to_manifest()["model"]
            == "meta-llama/Llama-3.1-8B-Instruct"
        )

    def test_task_decorator_rejects_model(self):
        from runpod.apps.app import App

        app = App("modeltest")

        # model= is not a parameter of @app.task; passed via a kwargs
        # dict so the intentional misuse is constructed at runtime
        kwargs = {"name": "train", "gpu": "4090", "model": Model("org/name")}
        with pytest.raises(TypeError, match="model"):

            @app.task(**kwargs)
            def train():
                pass

    def test_task_spec_rejects_model(self):
        from runpod.apps.errors import InvalidResourceError
        from runpod.apps.spec import ResourceKind, ResourceSpec

        with pytest.raises(InvalidResourceError, match="queue and api"):
            ResourceSpec(
                kind=ResourceKind.TASK, name="train", model=Model("org/name")
            )
