# Projects

A Runpod project is a single folder that contains all the files needed to create and run a serverless worker.

## Convert existing worker to a project

If you already have a worker, you can convert it to a project by navigating to the root of the worker folder and running `runpod project new --init`.

You may need to update the default configuration within `runpod.toml` to match your project structure.

## Ignore Files and Folders

Create a `.runpodignore` file in the root of your project to ignore files and folders from being uploaded to the Runpod platform, the same file will also be used to ignore files that should not trigger an API server reload.

### Flash deployment artifacts

`rp flash deploy` and `rp flash deploy --build-only` apply git-style patterns to
source files, using `pathspec`. Precedence, from lowest to highest, is:

1. Default local-file exclusions: virtual environments, Python caches, test
   directories and test modules, `node_modules`, `.DS_Store`, and `*.tar.gz`.
2. `.gitignore` files within the project, with deeper files overriding parent
   rules for their subtree.
3. The project-root `.runpodignore`.

Ancestor and global Git ignore files are not read. Within each ignore file, the
last matching rule wins. `/` anchors a pattern to that ignore file's directory;
trailing `/` matches directories; `**`, comments, escaped characters and `!`
negation follow Git ignore syntax. Excluded directories are not traversed, so
re-include the parent before its files. For example, to deploy a fixture from
the otherwise excluded `tests` directory:

```gitignore
!tests/
tests/*
!tests/fixture.json
```

Some safeguards cannot be overridden by negation: `.git`, `.runpod`, `.flash`,
the root `env/` and `runpod_manifest.json` paths, and credential-like source
paths. Credential safeguards include `.env` and its variants, `*.env` and its
variants, `*.pem`, `*.key`, SSH private-key names, `.ssh`, `.aws`, `.azure`,
`.kube`, `.docker/config.json`, `.netrc`, `.npmrc`, `.pypirc`, `.git-credentials`,
`.boto`, `credentials`/`credentials.*`, `secrets`/`secrets.*`, and
`service-account*.json`/`service_account*.json`. Use worker environment variables
or a secret store for credentials instead of packaging them with source.

These are filename safeguards, not secret scanning: credentials embedded in
ordinary source or differently named files can still be uploaded. Review the
build-only artifact before deployment.

The generated manifest and vendored `env/` are added separately and cannot be
replaced by source files. Source ignore rules, including the PEM/key safeguards,
do not apply to vendored dependencies, so dependency CA bundles are preserved.
The output artifact itself and the dependency build directory are never copied
back into source. Source and dependency symlinks are omitted, including
project-contained links and linked ignore files; directory links are not
traversed. Hardlinked regular files are stored as independent regular members,
so the archive requires no link extraction support.
