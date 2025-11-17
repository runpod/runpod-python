#!/usr/bin/env python3
"""
Compare cold start benchmark results between two runs.

Usage:
    python scripts/compare_benchmarks.py baseline.json optimized.json
    python scripts/compare_benchmarks.py benchmark_results/cold_start_1234.json benchmark_results/cold_start_5678.json
"""

import json
import sys
from pathlib import Path


def load_benchmark(file_path: str) -> dict:
    """Load benchmark results from JSON file."""
    with open(file_path) as f:
        return json.load(f)


def calculate_improvement(baseline: float, optimized: float) -> dict:
    """Calculate improvement metrics."""
    diff = baseline - optimized
    percent = (diff / baseline) * 100 if baseline > 0 else 0

    return {
        "diff_ms": round(diff, 2),
        "percent": round(percent, 2),
        "improved": diff > 0,
    }


def compare_benchmarks(baseline_file: str, optimized_file: str):
    """Compare two benchmark results and print analysis."""
    baseline = load_benchmark(baseline_file)
    optimized = load_benchmark(optimized_file)

    print("=" * 70)
    print("COLD START BENCHMARK COMPARISON")
    print("=" * 70)
    print(f"\nBaseline:  {baseline_file}")
    print(f"Optimized: {optimized_file}")
    print()

    # Compare main measurements
    print("IMPORT TIME COMPARISON")
    print("-" * 70)
    print(
        f"{'Metric':<25} {'Baseline':>12} {'Optimized':>12} {'Δ ms':>10} {'Δ %':>8}"
    )
    print("-" * 70)

    measurements = baseline["measurements"]
    opt_measurements = optimized["measurements"]

    total_improvement_ms = 0
    total_baseline_ms = 0

    for key in sorted(measurements.keys()):
        if key in opt_measurements:
            baseline_val = measurements[key]["mean"]
            optimized_val = opt_measurements[key]["mean"]
            improvement = calculate_improvement(baseline_val, optimized_val)

            symbol = "↓" if improvement["improved"] else "↑"
            color = "\033[92m" if improvement["improved"] else "\033[91m"
            reset = "\033[0m"

            print(
                f"{key:<25} {baseline_val:>10.2f}ms {optimized_val:>10.2f}ms "
                f"{color}{symbol}{improvement['diff_ms']:>8.2f}ms {improvement['percent']:>6.2f}%{reset}"
            )

            if key == "runpod_total":
                total_improvement_ms = improvement["diff_ms"]
                total_baseline_ms = baseline_val

    print("-" * 70)

    # Module counts
    print("\nMODULE LOAD COMPARISON")
    print("-" * 70)

    baseline_counts = baseline.get("module_counts", {})
    opt_counts = optimized.get("module_counts", {})

    if baseline_counts and opt_counts:
        total_diff = baseline_counts["total"] - opt_counts["total"]
        filtered_diff = baseline_counts["filtered"] - opt_counts["filtered"]

        print(f"Total modules loaded:")
        print(
            f"  Baseline:  {baseline_counts['total']:>4}  Optimized: {opt_counts['total']:>4}  Δ: {total_diff:>4}"
        )
        print(f"Runpod modules loaded:")
        print(
            f"  Baseline:  {baseline_counts['filtered']:>4}  Optimized: {opt_counts['filtered']:>4}  Δ: {filtered_diff:>4}"
        )

    # Lazy loading checks
    print("\nLAZY LOADING STATUS")
    print("-" * 70)

    checks = [
        ("paramiko_eagerly_loaded", "Paramiko"),
        ("ssh_cli_loaded", "SSH CLI"),
    ]

    for key, label in checks:
        baseline_loaded = baseline.get(key, False)
        opt_loaded = optimized.get(key, False)

        baseline_status = "LOADED" if baseline_loaded else "NOT LOADED"
        opt_status = "LOADED" if opt_loaded else "NOT LOADED"

        if baseline_loaded and not opt_loaded:
            status_symbol = "✓ NOW LAZY"
            color = "\033[92m"
        elif not baseline_loaded and opt_loaded:
            status_symbol = "✗ NOW EAGER"
            color = "\033[91m"
        else:
            status_symbol = "- NO CHANGE"
            color = "\033[93m"

        reset = "\033[0m"
        print(
            f"{label:<20} Baseline: {baseline_status:<12} Optimized: {opt_status:<12} {color}{status_symbol}{reset}"
        )

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    if total_improvement_ms > 0:
        percent_improvement = (
            total_improvement_ms / total_baseline_ms
        ) * 100
        print(f"✓ Cold start improved by {total_improvement_ms:.2f}ms")
        print(
            f"✓ That's a {percent_improvement:.1f}% improvement over baseline"
        )
        print(
            f"✓ Baseline: {total_baseline_ms:.2f}ms → Optimized: {total_baseline_ms - total_improvement_ms:.2f}ms"
        )
    elif total_improvement_ms < 0:
        print(
            f"✗ Cold start regressed by {abs(total_improvement_ms):.2f}ms"
        )
        print("  Review changes - performance got worse!")
    else:
        print("- No significant change in cold start time")

    print("=" * 70)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python compare_benchmarks.py <baseline.json> <optimized.json>")
        sys.exit(1)

    baseline_file = sys.argv[1]
    optimized_file = sys.argv[2]

    if not Path(baseline_file).exists():
        print(f"Error: Baseline file not found: {baseline_file}")
        sys.exit(1)

    if not Path(optimized_file).exists():
        print(f"Error: Optimized file not found: {optimized_file}")
        sys.exit(1)

    compare_benchmarks(baseline_file, optimized_file)
