# Cold Start Benchmarking

Performance benchmarking tools for measuring and comparing cold start times across different code changes.

## Quick Start

```bash
# Run benchmark on current branch
uv run pytest tests/test_performance/test_cold_start.py

# Compare two branches
./scripts/benchmark_cold_start.sh main my-feature-branch

# Compare two existing result files
uv run python scripts/compare_benchmarks.py benchmark_results/cold_start_baseline.json benchmark_results/cold_start_latest.json
```

## What Gets Measured

- **Import times**: `import runpod`, `import runpod.serverless`, `import runpod.endpoint`
- **Module counts**: Total modules loaded and runpod-specific modules
- **Lazy loading status**: Whether paramiko and SSH CLI are eagerly or lazy-loaded
- **Statistics**: Min, max, mean, median across 10 iterations per measurement

## Tools

### 1. test_cold_start.py

Core benchmark test that measures import performance in fresh Python subprocesses.

```bash
# Run as pytest test
uv run pytest tests/test_performance/test_cold_start.py -v

# Run as standalone script
uv run python tests/test_performance/test_cold_start.py

# Results saved to:
# - benchmark_results/cold_start_<timestamp>.json
# - benchmark_results/cold_start_latest.json (always latest)
```

**Output Example:**
```
Running cold start benchmarks...
------------------------------------------------------------
Measuring 'import runpod'...
  Mean: 273.29ms
Measuring 'import runpod.serverless'...
  Mean: 332.18ms
Counting loaded modules...
  Total modules: 582
  Runpod modules: 46
Checking if paramiko is eagerly loaded...
  Paramiko loaded: False
```

### 2. benchmark_cold_start.sh

Automated benchmark runner that handles git branch switching, dependency installation, and result collection.

```bash
# Run on current branch (no git operations)
./scripts/benchmark_cold_start.sh

# Run on specific branch
./scripts/benchmark_cold_start.sh main

# Compare two branches (runs both, then compares)
./scripts/benchmark_cold_start.sh main feature/lazy-loading
```

**Features:**
- Automatic stash/unstash of uncommitted changes
- Dependency installation per branch
- Safe branch switching with restoration
- Timestamped result files
- Automatic comparison when comparing branches

**Safety:**
- Stashes uncommitted changes before switching branches
- Restores original branch after completion
- Handles errors gracefully

### 3. compare_benchmarks.py

Analyzes and visualizes differences between two benchmark runs with colored terminal output.

```bash
uv run python scripts/compare_benchmarks.py <baseline.json> <optimized.json>
```

**Output Example:**
```
======================================================================
COLD START BENCHMARK COMPARISON
======================================================================

IMPORT TIME COMPARISON
----------------------------------------------------------------------
Metric                        Baseline    Optimized       Δ ms      Δ %
----------------------------------------------------------------------
runpod_total                  285.64ms     273.29ms ↓  12.35ms   4.32%
runpod_serverless             376.33ms     395.14ms ↑ -18.81ms  -5.00%
runpod_endpoint               378.61ms     399.36ms ↑ -20.75ms  -5.48%

MODULE LOAD COMPARISON
----------------------------------------------------------------------
Total modules loaded:
  Baseline:   698  Optimized:  582  Δ:  116
Runpod modules loaded:
  Baseline:    48  Optimized:   46  Δ:    2

LAZY LOADING STATUS
----------------------------------------------------------------------
Paramiko             Baseline: LOADED       Optimized: NOT LOADED   ✓ NOW LAZY
SSH CLI              Baseline: LOADED       Optimized: NOT LOADED   ✓ NOW LAZY

======================================================================
SUMMARY
======================================================================
✓ Cold start improved by 12.35ms
✓ That's a 4.3% improvement over baseline
✓ Baseline: 285.64ms → Optimized: 273.29ms
======================================================================
```

**Color coding:**
- Green: Improvements (faster times, lazy loading achieved)
- Red: Regressions (slower times, eager loading introduced)
- Yellow: No change

## Result Files

All benchmark results are saved to `benchmark_results/` (gitignored).

**File naming:**
- `cold_start_<timestamp>.json` - Timestamped result
- `cold_start_latest.json` - Always contains most recent result
- `cold_start_baseline.json` - Manually saved baseline for comparison

**JSON structure:**
```json
{
  "timestamp": 1763179522.0437188,
  "python_version": "3.8.20 (default, Oct  2 2024, 16:12:59) [Clang 18.1.8 ]",
  "measurements": {
    "runpod_total": {
      "min": 375.97,
      "max": 527.9,
      "mean": 393.91,
      "median": 380.4,
      "iterations": 10
    }
  },
  "module_counts": {
    "total": 698,
    "filtered": 48
  },
  "paramiko_eagerly_loaded": true,
  "ssh_cli_loaded": true
}
```

## Common Workflows

### Testing a Performance Optimization

```bash
# 1. Save baseline on main branch
git checkout main
./scripts/benchmark_cold_start.sh
cp benchmark_results/cold_start_latest.json benchmark_results/cold_start_baseline.json

# 2. Switch to feature branch
git checkout feature/my-optimization

# 3. Run benchmark and compare
./scripts/benchmark_cold_start.sh
uv run python scripts/compare_benchmarks.py \
  benchmark_results/cold_start_baseline.json \
  benchmark_results/cold_start_latest.json
```

### Comparing Multiple Approaches

```bash
# Compare three different optimization branches
./scripts/benchmark_cold_start.sh main > results_main.txt
./scripts/benchmark_cold_start.sh feature/approach-1 > results_1.txt
./scripts/benchmark_cold_start.sh feature/approach-2 > results_2.txt

# Then compare each against baseline
uv run python scripts/compare_benchmarks.py \
  benchmark_results/cold_start_main_*.json \
  benchmark_results/cold_start_approach-1_*.json
```

### CI/CD Integration

Add to your GitHub Actions workflow:

```yaml
- name: Run cold start benchmark
  run: |
    uv run pytest tests/test_performance/test_cold_start.py --timeout=120

- name: Upload benchmark results
  uses: actions/upload-artifact@v3
  with:
    name: benchmark-results
    path: benchmark_results/cold_start_latest.json
```

## Performance Targets

Based on testing with Python 3.8:

- **Cold start (import runpod)**: < 300ms (mean)
- **Serverless import**: < 400ms (mean)
- **Module count**: < 600 total modules
- **Test assertion**: Fails if import > 1000ms

## Interpreting Results

### Import Time Variance

Subprocess-based measurements have inherent variance:
- First run in sequence: Often 20-50ms slower (Python startup overhead)
- Subsequent runs: More stable
- **Use median or mean** for comparison, not single runs

### Module Count

- **Fewer modules = faster cold start**: Each module has import overhead
- **Runpod-specific modules**: Should be minimal (40-50)
- **Total modules**: Includes stdlib and dependencies
- **Target reduction**: Removing 100+ modules typically saves 10-30ms

### Lazy Loading Validation

- `paramiko_eagerly_loaded: false` - Good for serverless workers
- `ssh_cli_loaded: false` - Good for SDK users
- These should only be `true` when CLI commands are invoked

## Troubleshooting

### High Variance in Results

If you see >100ms variance between runs:
- System is under load
- Disk I/O contention
- Python bytecode cache issues

**Solution:** Run multiple times and use median values.

### benchmark_cold_start.sh Fails

```bash
# Check git status
git status

# Manually restore if script failed mid-execution
git checkout <original-branch>
git stash pop
```

### Import Errors During Benchmark

Ensure dependencies are installed:
```bash
uv sync --group test
```

## Benchmark Accuracy

- **Iterations**: 10 per measurement (configurable in test)
- **Process isolation**: Each measurement uses fresh subprocess
- **Python cache**: Cleared by subprocess creation
- **System state**: Cannot control OS-level caching

For production performance testing, consider:
- Running on CI with consistent environment
- Multiple runs at different times
- Comparing trends over multiple commits
