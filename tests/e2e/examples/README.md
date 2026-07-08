# e2e example suite

self-asserting apps exercising every sdk feature against production.
each file runs via `rp dev <file> --once` and fails loudly when a
result is wrong, so the set doubles as a release gate.

```bash
python tests/e2e/examples/run_all.py            # everything
python tests/e2e/examples/run_all.py --jobs 4   # parallel
python tests/e2e/examples/run_all.py 01 07      # subset by prefix
```

prerequisites:

```bash
rp login
rp secret add ex-demo-secret --value anything   # for 09_secrets
```

`12_custom_image` installs the runpod package at cold start; until the
release with `runpod.runtimes` is on pypi, point the bootstrap at the
branch tarball (plain https, not git+ — slim images have no git):

```bash
export RUNPOD_PACKAGE_SPEC=https://github.com/runpod/runpod-python/archive/refs/heads/feat/apps-sdk.tar.gz
```
