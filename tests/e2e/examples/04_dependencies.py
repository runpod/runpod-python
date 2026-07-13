"""pip and apt dependencies, available on the worker without a custom image.

    rp dev tests/e2e/examples/04_dependencies.py --once
"""

import runpod

app = runpod.App("ex-deps")


@app.queue(cpu="cpu3c-1-2", dependencies=["pyfiglet"])
def banner(word: str):
    import pyfiglet

    art = pyfiglet.figlet_format(word)
    print(art)
    return len(art)


@app.queue(cpu="cpu3c-1-2", system_dependencies=["jq"])
def jq_version():
    import subprocess

    out = subprocess.run(["jq", "--version"], capture_output=True, text=True)
    return out.stdout.strip()


@runpod.local_entrypoint
def main():
    assert banner.remote("hi") > 0
    version = jq_version.remote()
    print("jq:", version)
    assert version.startswith("jq-")


if __name__ == "__main__":
    main()
