# [BETA] | RunPod Python CLI Reference

Note: This CLI is not the same as runpodctl and provides a different set of features.

## Getting Started

![runpod --help](demos/help.gif)

### Configure

![runpod config](demos/config.gif)

Store your RunPod API key by running `runpod config`. Optionally you can also call the command with your API key `runpod config YOUR_API_KEY` or include the `--profile` to save multiple keys (stored under "default" profile is not specified) Credentials are stored in `~/.runpod/config.toml`.

![runpod ssh add-key](demos/ssh.gif)

Add a SSH key to you account by running `runpod ssh add-key`. To specify and existing key pass in `--key` or `--key-file` to use a file. Keys are stored in `~/.runpod/ssh/`.  If no key is specified a new one will be generated and stored.
