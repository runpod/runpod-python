"""platform-cached models: weights staged on hosts before workers start.

a Model references huggingface weights by repo id. attached to a
resource, the platform stages the weights on the host and defers
worker start until they are ready, so cold starts never download
model files.

    llama = runpod.Model("meta-llama/Llama-3.1-8B-Instruct")

    @app.queue(gpu="H100", model=llama, env={"HF_TOKEN": runpod.Secret("hf")})
    def chat(prompt: str):
        llm = vllm.LLM(model=str(llama.path))

inside the worker the weights appear in two layouts:
  - runpod store: /runpod/model-store/huggingface/{org}/{name}/{revision}
    (Model.path, revision from the MODEL_REVISION env var)
  - hf cache:     /runpod-volume/huggingface-cache/hub/models--{org}--{name}
    (transformers/vllm find it via the standard cache convention)

gated models need an HF_TOKEN env var on the same resource (a Secret
reference works; the platform decrypts it for validation).
"""

import os
import re
from pathlib import Path
from typing import Optional

from .errors import AppError

MODEL_STORE_ROOT = Path("/runpod/model-store/huggingface")
HF_CACHE_ROOT = Path("/runpod-volume/huggingface-cache")

_REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")


class ModelError(AppError):
    pass


class Model:
    """a huggingface model reference, staged by the platform."""

    def __init__(self, reference: str):
        if not reference or not isinstance(reference, str):
            raise ModelError("model reference must be a non-empty string")
        repo, _, revision = reference.partition(":")
        if not _REPO_RE.match(repo):
            raise ModelError(
                f"invalid model reference '{reference}': expected "
                f"'owner/name' or 'owner/name:revision'"
            )
        self.reference = reference
        self.owner, self.name = repo.split("/")
        self.revision = revision or None

    @property
    def path(self) -> Path:
        """the staged weights directory inside the worker.

        the platform resolves the exact revision at deploy time and
        exposes it via MODEL_REVISION; outside a worker (or before the
        mount exists) this still forms the correct path shape.
        """
        revision = os.environ.get("MODEL_REVISION") or self.revision or ""
        base = MODEL_STORE_ROOT / self.owner / self.name
        return base / revision if revision else base

    @property
    def hf_cache_path(self) -> Path:
        """the huggingface-convention cache directory for this model."""
        return (
            HF_CACHE_ROOT / "hub" / f"models--{self.owner}--{self.name}"
        )

    def __repr__(self) -> str:
        return f"<Model {self.reference!r}>"


def model_reference(model) -> Optional[str]:
    """normalize a spec's model field to the api-facing reference."""
    if model is None:
        return None
    if isinstance(model, Model):
        return model.reference
    if isinstance(model, str):
        return Model(model).reference
    raise ModelError(
        f"model must be a runpod.Model or 'owner/name' string, "
        f"got {type(model).__name__}"
    )
