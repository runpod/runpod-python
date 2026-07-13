# e2e example suite

self-asserting apps exercising every sdk feature against production.
each file fails loudly when a result is wrong, so the set doubles as a
release gate. every example carries both a `@runpod.local_entrypoint`
and an `if __name__ == "__main__"` block, so the same `main()` runs in
three modes:

| mode   | command              | what it exercises                             |
| ------ | -------------------- | --------------------------------------------- |
| dev    | `rp dev <file> --once` | ephemeral endpoints, entrypoint, teardown   |
| deploy | `rp deploy <file>`     | persistent endpoints for the app            |
| invoke | `python3 <file>`       | `main()` against the deployed endpoints     |

`run_all.sh` runs every example through all three (the deploy phase does
deploy -> invoke -> undeploy per file, leaving nothing standing):

```bash
./tests/e2e/examples/run_all.sh          # everything, every mode
./tests/e2e/examples/run_all.sh 01 07    # subset by prefix

SKIP_DEV=1    ./tests/e2e/examples/run_all.sh   # deploy/invoke only
SKIP_DEPLOY=1 ./tests/e2e/examples/run_all.sh   # dev only
KEEP=1        ./tests/e2e/examples/run_all.sh   # leave endpoints up
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
