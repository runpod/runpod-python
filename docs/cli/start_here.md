# Runpod CLI Reference

Note: This CLI is not the same as runpodctl and provides a different set of features.

## Getting Started

![rp --help](demos/help.gif)

### Authenticate

Store your Runpod API key by running `rp login` (browser approval) or `rp login --api-key YOUR_KEY`. Credentials are stored in `~/.runpod/config.toml`.

### SSH

Add an SSH key to your account by running `rp ssh add`. To use an existing key pass `--key` or `--key-file`. Keys are stored in `~/.runpod/ssh/`. If no key is specified a new one is generated and stored.

Once a key is added, open a terminal on any pod with `rp ssh <pod_id>` (or the equivalent `rp pod connect <pod_id>`).

## Apps

An app is a collection of Python functions that run on Runpod. Scaffold one with `rp init`, iterate on it live with `rp dev main.py`, and ship it with `rp deploy`. See the [README](../../README.md) for the full workflow.
