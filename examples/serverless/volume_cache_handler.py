"""Warm-cache model weights across cold starts with VolumeCache.

`VolumeCache` keeps a browsable mirror of local cache directories on a mounted
network volume and reconciles the two on each use: it restores cached files on
cold start (`hydrate`) and syncs new downloads back afterward (`sync`). Wrapping
a model load in `with VolumeCache(...)` turns a repeated multi-GB download into a
one-time cost per endpoint.

Why not just point HF_HOME at the volume? Because then every read hits the
network mount. Here the Hugging Face cache stays on fast local disk and
VolumeCache mirrors it to the volume, so inference reads are local while cold
starts stay warm.

Requirements to run:
- Attach a network volume to the endpoint (mounted at /runpod-volume).
- RUNPOD_ENDPOINT_ID is set automatically on Runpod serverless (it scopes the
  cache per endpoint). Without a mounted volume or endpoint id, VolumeCache is a
  safe no-op and the handler still works.
- pip install "transformers" "torch" (the model library used below).

Local test:
    python volume_cache_handler.py --rp_serve_api
"""

import os

import runpod
from runpod.serverless import VolumeCache

# Hugging Face caches models here by default. Keep it on local disk and let
# VolumeCache mirror it to the network volume.
HF_CACHE = os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface"))

# Hydrate the cache from the volume before loading, then sync new files back on
# exit. On a warm endpoint the model is already local and nothing is downloaded.
with VolumeCache(dirs=[HF_CACHE]):
    from transformers import pipeline

    classifier = pipeline("sentiment-analysis")


def handler(job):
    """Classify the sentiment of an input string."""
    text = job["input"].get("text", "")
    return {"input": text, "predictions": classifier(text)}


runpod.serverless.start({"handler": handler})
