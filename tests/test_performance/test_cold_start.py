"""
Cold start performance benchmarks for runpod package.

These tests measure import times and memory usage to track cold start
performance across different branches and changes.
"""

import json
import subprocess
import sys
import time
from pathlib import Path


def measure_import_time(module_name: str, iterations: int = 10) -> dict:
    """
    Measure the time it takes to import a module in a fresh Python process.

    Args:
        module_name: Name of the module to import
        iterations: Number of iterations to average

    Returns:
        dict with min, max, mean, and median times in milliseconds
    """
    times = []

    for _ in range(iterations):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                f"import time; start = time.perf_counter(); import {module_name}; "
                f"print((time.perf_counter() - start) * 1000)",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:
            times.append(float(result.stdout.strip()))
        else:
            raise RuntimeError(
                f"Failed to import {module_name}: {result.stderr}"
            )

    times.sort()
    return {
        "min": round(times[0], 2),
        "max": round(times[-1], 2),
        "mean": round(sum(times) / len(times), 2),
        "median": round(
            times[len(times) // 2] if len(times) % 2 == 1 else
            (times[len(times) // 2 - 1] + times[len(times) // 2]) / 2,
            2
        ),
        "iterations": iterations,
    }


def count_loaded_modules(module_name: str, module_filter: str = None) -> dict:
    """
    Count how many modules are loaded after importing a module.

    Args:
        module_name: Name of the module to import
        module_filter: Optional filter to count specific module namespaces

    Returns:
        dict with total count and filtered count
    """
    script = f"""
import sys
import {module_name}

all_modules = list(sys.modules.keys())
total = len(all_modules)

if {repr(module_filter)}:
    filtered = [m for m in all_modules if {repr(module_filter)} in m]
    print(f"{{total}},{{len(filtered)}}")
else:
    print(f"{{total}},0")
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
    )

    if result.returncode == 0:
        total, filtered = result.stdout.strip().split(",")
        return {"total": int(total), "filtered": int(filtered)}
    else:
        raise RuntimeError(f"Failed to count modules: {result.stderr}")


def check_module_loaded(import_statement: str, module_to_check: str) -> bool:
    """
    Check if a specific module is loaded after an import statement.

    Args:
        import_statement: Python import statement to execute
        module_to_check: Module name to check in sys.modules

    Returns:
        True if module is loaded, False otherwise
    """
    script = f"""
import sys
{import_statement}
print('yes' if '{module_to_check}' in sys.modules else 'no')
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
    )

    if result.returncode == 0:
        return result.stdout.strip() == "yes"
    else:
        raise RuntimeError(f"Failed to check module: {result.stderr}")


def run_full_benchmark() -> dict:
    """
    Run a comprehensive cold start benchmark suite.

    Returns:
        dict with all benchmark results
    """
    print("Running cold start benchmarks...")
    print("-" * 60)

    benchmark_results = {
        "timestamp": time.time(),
        "python_version": sys.version,
        "measurements": {},
    }

    # Measure main runpod import
    print("Measuring 'import runpod'...")
    benchmark_results["measurements"]["runpod_total"] = measure_import_time(
        "runpod"
    )
    print(
        f"  Mean: {benchmark_results['measurements']['runpod_total']['mean']}ms"
    )

    # Measure serverless-only import
    print("Measuring 'import runpod.serverless'...")
    benchmark_results["measurements"][
        "runpod_serverless"
    ] = measure_import_time("runpod.serverless")
    print(
        f"  Mean: {benchmark_results['measurements']['runpod_serverless']['mean']}ms"
    )

    # Measure endpoint import
    print("Measuring 'import runpod.endpoint'...")
    benchmark_results["measurements"]["runpod_endpoint"] = measure_import_time(
        "runpod.endpoint"
    )
    print(
        f"  Mean: {benchmark_results['measurements']['runpod_endpoint']['mean']}ms"
    )

    # Count loaded modules
    print("Counting loaded modules...")
    module_counts = count_loaded_modules("runpod", "runpod")
    benchmark_results["module_counts"] = module_counts
    print(f"  Total modules: {module_counts['total']}")
    print(f"  Runpod modules: {module_counts['filtered']}")

    # Check if paramiko is loaded
    print("Checking if paramiko is eagerly loaded...")
    paramiko_loaded = check_module_loaded("import runpod", "paramiko")
    benchmark_results["paramiko_eagerly_loaded"] = paramiko_loaded
    print(f"  Paramiko loaded: {paramiko_loaded}")

    # Check if CLI modules are loaded
    print("Checking if CLI modules are loaded...")
    cli_loaded = check_module_loaded("import runpod", "runpod.cli.groups.ssh")
    benchmark_results["ssh_cli_loaded"] = cli_loaded
    print(f"  SSH CLI loaded: {cli_loaded}")

    # Measure heavy dependencies if they're loaded
    if paramiko_loaded:
        print("Measuring 'import paramiko' (since it's loaded)...")
        try:
            benchmark_results["measurements"][
                "paramiko"
            ] = measure_import_time("paramiko")
            print(
                f"  Mean: {benchmark_results['measurements']['paramiko']['mean']}ms"
            )
        except Exception as e:
            print(f"  Failed: {e}")

    print("-" * 60)
    print("Benchmark complete!")

    return benchmark_results


def test_cold_start_benchmark(tmp_path):
    """
    Pytest test that runs the benchmark and saves results to a file.
    """
    results = run_full_benchmark()

    # Save results to a timestamped file
    output_dir = Path("benchmark_results")
    output_dir.mkdir(exist_ok=True)

    timestamp = int(time.time())
    output_file = output_dir / f"cold_start_{timestamp}.json"

    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nResults saved to: {output_file}")

    # Also save as latest for easy comparison
    latest_file = output_dir / "cold_start_latest.json"
    with open(latest_file, "w") as f:
        json.dump(results, f, indent=2)

    # Assert that import time is reasonable (adjust threshold as needed)
    assert (
        results["measurements"]["runpod_total"]["mean"] < 1000
    ), "Import time exceeds 1000ms"


if __name__ == "__main__":
    results = run_full_benchmark()
    print("\nFull Results:")
    print(json.dumps(results, indent=2))
