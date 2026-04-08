#!/bin/bash
# Run cold start benchmarks on different git branches and compare results.
#
# Usage:
#   ./scripts/benchmark_cold_start.sh                    # Run on current branch
#   ./scripts/benchmark_cold_start.sh main               # Run on main branch
#   ./scripts/benchmark_cold_start.sh main feat/lazy    # Compare two branches

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RESULTS_DIR="$PROJECT_ROOT/benchmark_results"

mkdir -p "$RESULTS_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to run benchmark on a branch
run_benchmark_on_branch() {
    local branch=$1
    local output_file=$2

    log_info "Running benchmark on branch: $branch"

    # Save current state
    local current_branch=$(git branch --show-current)
    local has_changes=$(git status --porcelain)

    if [ -n "$has_changes" ]; then
        log_warn "Working directory has uncommitted changes"
        log_warn "Stashing changes..."
        git stash push -m "Benchmark stash $(date +%s)"
    fi

    # Checkout target branch
    if [ "$branch" != "$current_branch" ]; then
        log_info "Checking out branch: $branch"
        git checkout "$branch"
    fi

    # Install dependencies
    log_info "Installing dependencies..."
    uv sync --group test > /dev/null 2>&1

    # Run benchmark
    log_info "Running benchmark..."
    cd "$PROJECT_ROOT"
    uv run python tests/test_performance/test_cold_start.py > /dev/null 2>&1

    # Copy latest result to output file
    if [ -f "$RESULTS_DIR/cold_start_latest.json" ]; then
        cp "$RESULTS_DIR/cold_start_latest.json" "$output_file"
        log_info "Results saved to: $output_file"
    else
        log_error "Benchmark failed to produce results"
        return 1
    fi

    # Restore original state
    if [ "$branch" != "$current_branch" ]; then
        log_info "Returning to branch: $current_branch"
        git checkout "$current_branch"
    fi

    if [ -n "$has_changes" ]; then
        log_info "Restoring stashed changes..."
        git stash pop > /dev/null 2>&1
    fi
}

# Main script logic
if [ $# -eq 0 ]; then
    # Run on current branch only
    log_info "Running benchmark on current branch"
    current_branch=$(git branch --show-current || echo "detached")
    output_file="$RESULTS_DIR/cold_start_${current_branch//\//_}_$(date +%s).json"

    uv run python tests/test_performance/test_cold_start.py

    if [ -f "$RESULTS_DIR/cold_start_latest.json" ]; then
        cp "$RESULTS_DIR/cold_start_latest.json" "$output_file"
        log_info "Results saved to: $output_file"
        log_info "Latest results: $RESULTS_DIR/cold_start_latest.json"
    fi

elif [ $# -eq 1 ]; then
    # Run on specified branch
    branch=$1
    output_file="$RESULTS_DIR/cold_start_${branch//\//_}_$(date +%s).json"
    run_benchmark_on_branch "$branch" "$output_file"

elif [ $# -eq 2 ]; then
    # Compare two branches
    baseline_branch=$1
    optimized_branch=$2

    log_info "Comparing branches: $baseline_branch vs $optimized_branch"

    baseline_file="$RESULTS_DIR/cold_start_baseline_$(date +%s).json"
    optimized_file="$RESULTS_DIR/cold_start_optimized_$(date +%s).json"

    # Run benchmarks
    run_benchmark_on_branch "$baseline_branch" "$baseline_file"
    run_benchmark_on_branch "$optimized_branch" "$optimized_file"

    # Compare results
    log_info "Comparing results..."
    uv run python "$SCRIPT_DIR/compare_benchmarks.py" "$baseline_file" "$optimized_file"

else
    log_error "Invalid number of arguments"
    echo "Usage:"
    echo "  $0                           # Run on current branch"
    echo "  $0 <branch>                  # Run on specified branch"
    echo "  $0 <baseline> <optimized>    # Compare two branches"
    exit 1
fi
