# [BETA] | RunPod Python CLI Reference

Note: This CLI is not the same as runpodctl and provides a different set of features.

## Getting Started

![runpod --help](demos/help.gif)

### Configure

![runpod config](demos/config.gif)

Store your RunPod API key by running `runpod config`. Optionally you can also call the command with your API key `runpod config YOUR_API_KEY` or include the `--profile` to save multiple keys (stored under "default" profile is not specified) Credentials are stored in `~/.runpod/config.toml`.

![runpod ssh add-key](demos/ssh.gif)

Add a SSH key to you account by running `runpod ssh add-key`. To specify and existing key pass in `--key` or `--key-file` to use a file. Keys are stored in `~/.runpod/ssh/`.  If no key is specified a new one will be generated and stored.

## RunPod Project

A "project" is the start of a serverless worker. To get started call `runpod project new`, you will be asked a few questions about the project you are creating, a project folder will be created. You can now navigate into your repo and run `runpod project start`.

Once you are finished developing you can run `runpod project deploy` to deploy your project and create an endpoint.
