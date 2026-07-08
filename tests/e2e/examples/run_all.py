#!/usr/bin/env python3
"""run every example against prod as an end-to-end suite.

each example is executed with `rp dev <file> --once`, which provisions
real endpoints/pods, runs the example's local_entrypoint (every example
asserts on its results), and cleans up. requires `rp login` and, for
09_secrets, the demo secret:

    rp secret add ex-demo-secret --value anything

usage:
    python tests/e2e/examples/run_all.py                # everything
    python tests/e2e/examples/run_all.py 01 05 08      # by prefix
    python tests/e2e/examples/run_all.py --jobs 4      # parallel
"""

import argparse
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent
TIMEOUT_SECONDS = 2400


def find_examples(prefixes):
    files = sorted(
        p
        for p in HERE.glob("[0-9][0-9]_*.py")
        if not prefixes or any(p.name.startswith(pre) for pre in prefixes)
    )
    if not files:
        sys.exit(f"no examples match {prefixes}")
    return files


def run_one(path: Path):
    start = time.monotonic()
    proc = subprocess.run(
        ["rp", "dev", str(path), "--once"],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
    )
    elapsed = time.monotonic() - start
    ok = proc.returncode == 0
    return path.name, ok, elapsed, proc.stdout + proc.stderr


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prefixes", nargs="*", help="example prefixes, e.g. 01 08")
    parser.add_argument("--jobs", type=int, default=1, help="parallel sessions")
    args = parser.parse_args()

    files = find_examples(args.prefixes)
    print(f"running {len(files)} examples (jobs={args.jobs})\n")

    results = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for name, ok, elapsed, output in pool.map(run_one, files):
            mark = "PASS" if ok else "FAIL"
            print(f"  {mark}  {name}  ({elapsed:.0f}s)")
            results.append((name, ok, output))

    failures = [(n, out) for n, ok, out in results if not ok]
    print(f"\n{len(results) - len(failures)}/{len(results)} passed")
    for name, output in failures:
        print(f"\n--- {name} ---")
        print("\n".join(output.splitlines()[-30:]))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
