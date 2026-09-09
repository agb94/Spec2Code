#!/bin/sh
set -eu

output_dir=${COVERAGE_OUTPUT_DIR:-/results/full-suite}
timeout=${COVERAGE_TIMEOUT:-600}

case "$output_dir" in
    /results/*) ;;
    *)
        echo "COVERAGE_OUTPUT_DIR must be a child of /results: $output_dir" >&2
        exit 2
        ;;
esac

if [ "$#" -eq 0 ]; then
    set -- test
fi

/opt/coverage-venv/bin/python \
    /opt/spec2code/data/benchmark/dynamic_coverage/collect_dynamic_coverage.py \
    --repo-root /opt/spec2code \
    --httpd-src /opt/spec2code/data/repos/httpd/rawcode \
    --install /opt/httpd-coverage \
    --output-dir "$output_dir" \
    --pytest /opt/coverage-venv/bin/pytest \
    --llvm-profdata /usr/bin/llvm-profdata \
    --llvm-cov /usr/bin/llvm-cov \
    --group-parametrized \
    --timeout "$timeout" \
    "$@"
