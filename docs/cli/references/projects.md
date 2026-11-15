# Projects

A Runpod project is a single folder that contains all the files needed to create and run a serverless worker.

## Convert existing worker to a project

If you already have a worker, you can convert it to a project by navigating to the root of the worker folder and running `runpod project new --init`.

You may need to update the default configuration within `runpod.toml` to match your project structure.

## Ignore Files and Folders

Create a `.runpodignore` file in the root of your project to ignore files and folders from being uploaded to the Runpod platform, the same file will also be used to ignore files that should not trigger an API server reload.

### Flash deployment artifacts

`rp flash deploy` and `rp flash deploy --build-only` apply Git-style ignore
patterns in this order, with later rules taking precedence:

1. Default exclusions for local environments, caches, tests, and build archives.
2. Project `.gitignore` files, with deeper files overriding parents in their subtree.
3. The project-root `.runpodignore`.

Ancestor and global Git ignores are not read. Negation (`!`) can re-include
ordinary files, but excluded parent directories must also be re-included.

Negation cannot include `.git`, `.runpod`, `.flash`, the root `env/` or
`runpod_manifest.json`, or credential-like source paths such as `.env` variants,
PEM/key files, private SSH keys, cloud credential directories, and credentials,
secrets, or service-account files. These are filename safeguards, not secret
scanning; review the build-only artifact and supply credentials through worker
environment variables or a secret store.

The manifest and vendored `env/` are added separately. Source ignore rules do
not strip dependency CA bundles. Symlinks are omitted from source and
dependencies; the output artifact and dependency build directory are never
copied back into source.
