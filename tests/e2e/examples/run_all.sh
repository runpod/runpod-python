#!/usr/bin/env bash
# run every example against prod as an end-to-end suite, in three modes.
#
# each example is a self-asserting app with both a
# @runpod.local_entrypoint and an `if __name__ == "__main__"` block, so
# the same main() runs under all three:
#
#   dev     rp dev <file> --once   ephemeral endpoints, run entrypoint, tear down
#   deploy  rp deploy <file>       persistent endpoints for the app
#   invoke  python3 <file>         run main() against the deployed endpoints
#
# the deploy phase runs deploy -> invoke -> undeploy per file, so no
# endpoints are left standing. requires `rp login` and, for 09_secrets:
#
#     rp secret add ex-demo-secret --value anything
#
# output streams live so you see the exact cli output.
#
# usage:
#     ./run_all.sh                 # every example, every mode
#     ./run_all.sh 01 05 08        # subset by prefix
#
# env toggles:
#     SKIP_DEV=1     skip the rp dev phase
#     SKIP_DEPLOY=1  skip the rp deploy/python3 phase
#     KEEP=1         leave deployed endpoints up (skip undeploy)

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

files=()
for f in "$HERE"/[0-9][0-9]_*.py; do
    name="$(basename "$f")"
    if [ "$#" -eq 0 ]; then
        files+=("$f")
    else
        for pre in "$@"; do
            if [[ "$name" == "$pre"* ]]; then
                files+=("$f")
                break
            fi
        done
    fi
done

if [ "${#files[@]}" -eq 0 ]; then
    echo "no examples match: $*" >&2
    exit 1
fi

passed=()
failed=()

app_name_of() {
    sed -nE 's/.*App\("([^"]+)".*/\1/p' "$1" | head -1
}

# run one step; stream output live, record pass/fail as "<mode> <name>".
run_step() {
    local mode="$1" name="$2"
    shift 2
    echo "==================================================================="
    echo ">>> [$mode] $name"
    echo "==================================================================="
    local start rc elapsed
    start=$(date +%s)
    "$@"
    rc=$?
    elapsed=$(( $(date +%s) - start ))
    if [ "$rc" -eq 0 ]; then
        echo "<<< PASS  [$mode] $name  (${elapsed}s)"
        passed+=("$mode $name")
    else
        echo "<<< FAIL  [$mode] $name  (${elapsed}s, exit $rc)"
        failed+=("$mode $name")
    fi
    echo
    return "$rc"
}

echo "running ${#files[@]} examples"
echo

# ---------------------------------------------------------------- dev
if [ -z "${SKIP_DEV:-}" ]; then
    for f in "${files[@]}"; do
        run_step dev "$(basename "$f")" rp dev "$f" --once
    done
fi

# ------------------------------------------------- deploy -> invoke -> undeploy
if [ -z "${SKIP_DEPLOY:-}" ]; then
    for f in "${files[@]}"; do
        name="$(basename "$f")"
        app="$(app_name_of "$f")"

        if run_step deploy "$name" rp deploy "$f"; then
            run_step invoke "$name" python3 "$f"
            if [ -z "${KEEP:-}" ] && [ -n "$app" ]; then
                echo ">>> cleanup: rp undeploy -a $app -y"
                rp undeploy -a "$app" -y || echo "!!! cleanup failed for $app" >&2
                echo
            fi
        fi
    done
fi

# ------------------------------------------------------------- summary
total=$(( ${#passed[@]} + ${#failed[@]} ))
echo "==================================================================="
echo "${#passed[@]}/${total} steps passed"
if [ "${#failed[@]}" -gt 0 ]; then
    for s in "${failed[@]}"; do
        echo "  FAIL  $s"
    done
fi

[ "${#failed[@]}" -eq 0 ]
