# Dynamic Test Coverage for Function–Spec Mappings

Measurement date: 2026-09-09
Environment: Docker, Linux ARM64

## Goal

The goal is to find existing tests that execute the implementation functions mapped to each specification in `data/benchmark/mapping.csv`.

A test is recorded as a candidate for a specification when:

1. The test passes.
2. LLVM coverage reports that the test invocation executed the mapped function at least once.

This is dynamic candidate evidence, not proof that the test assertion semantically verifies every condition and outcome in the specification.

## Inputs

- Function–spec mapping: `data/benchmark/mapping.csv`
- Function metadata: `data/benchmark/all_func.csv`
- Extracted HTTP specifications: `data/rfc/extract_sr/http.csv`
- Apache httpd implementation and tests: `data/repos/httpd/rawcode`

The mapping contains 219 rows, 53 functions, and 119 unique specifications. Specification uniqueness is defined by `(spec_idx, sr_text)`.

## Docker Measurement

The Docker image builds Apache httpd and its relevant shared modules with Clang LLVM coverage instrumentation. It also installs the pytest and protocol-test dependencies needed by the full test tree.

Each logical pytest function is run in a separate pytest invocation. Parameter cases belonging to the same logical function are grouped together. Every invocation writes profiles using:

```text
LLVM_PROFILE_FILE=<per-test-directory>/%m-%p.profraw
```

`%p` separates Apache processes and `%m` separates instrumented executables and shared modules. The collector merges each test's profiles with `llvm-profdata`, exports function counts with `llvm-cov`, and matches the normalized function name and source file to `all_func.csv`. It then expands observed function–test hits through `mapping.csv` to produce spec–function–test candidates.

Profiles from failed or skipped tests are not accepted as candidate evidence. Raw profiles are deleted after successful export unless `--keep-profiles` is explicitly supplied.

### Run the full suite

From the repository root:

```sh
docker compose \
  -f data/benchmark/dynamic_coverage/compose.yaml \
  build coverage-all

docker compose \
  -f data/benchmark/dynamic_coverage/compose.yaml \
  run --rm coverage-all
```

The collector covers the complete pytest tree under `test`, including `core`, `http1`, `http2`, `md`, `proxy`, and `tls`. The default raw run is written to:

```text
data/benchmark/dynamic_coverage/docker/results/full-suite
```

The output directory must not already exist. Set `COVERAGE_OUTPUT_DIR` to a new path below `/results` for another run:

```sh
COVERAGE_OUTPUT_DIR=/results/full-suite-2 \
docker compose \
  -f data/benchmark/dynamic_coverage/compose.yaml \
  run --rm coverage-all
```

`COVERAGE_TIMEOUT` changes the default 600-second timeout per logical test. Additional arguments after `coverage-all` replace the default `test` scope with a pytest path or exact node ID.

The four C sources under `test/unit` are not pytest tests and are not collected. None directly targets the 53 functions in the mapping.

## Published Full-Suite Result

The final portable result is retained at:

```text
data/benchmark/dynamic_coverage/docker_full_suite
```

| Metric | Result | Rate |
|---|---:|---:|
| Logical tests passed | 365 / 601 | 60.7% |
| Logical tests skipped | 225 / 601 | 37.4% |
| Logical tests failed | 11 / 601 | 1.8% |
| Collected parameter cases | 945 | N/A |
| Mapped functions executed by passing tests | 51 / 53 | 96.2% |
| Mapping rows with candidate tests | 214 / 219 | 97.7% |
| Unique specs with candidate tests | 118 / 119 | 99.2% |
| Function–test execution pairs | 13,712 | N/A |
| Spec–function–test candidate rows | 61,085 | N/A |

### Result files

- `spec_test_candidates.csv`: the primary expanded spec → function → passing test evidence.
- `spec_coverage_summary.csv`: one row per original function–spec mapping row, including execution status and candidate tests.
- `function_test_coverage.csv`: observed function → passing test execution pairs.
- `function_coverage_summary.csv`: one row per mapped function, including execution status and candidate tests.
- `test_runs.csv`: status, duration, profile count, and mapped-function hit count for all 601 logical invocations.
- `summary.json`: machine-readable aggregate metrics.

### Missing candidate coverage

Two mapped functions were not executed by any passing test:

| function_id | Function | Source | Mapping rows |
|---:|---|---|---:|
| 12265 | `form_header_field` | `modules/http/http_filters.c:853` | 3 |
| 12340 | `ap_send_http_options` | `modules/http/http_protocol.c:908` | 2 |

These functions account for the five mapping rows without candidate tests. Four requirements are also mapped to another function that was executed. Therefore, only specification 30 has no candidate test through any mapped function:

> A server MUST NOT switch protocols unless the received message semantics can be honored by the new protocol; an OPTIONS request can be honored by any protocol.

## Test Status Limitations

The 11 failed tests were excluded from candidate evidence. Six are mod_md tests involving ACME challenge selection, error/backoff state, failover, or managed-domain completion. Five are TLS-package tests involving SNI error-log validation, curl verbose-output parsing, or Apache configuration startup.

The 225 skipped logical invocations are upstream condition-based skips. The largest groups require the unavailable `a2md` command, ACME External Account Binding, an OCSP responder, optional mod_tls, client certificates, private service credentials, or explicit stress-test settings. One logical invocation can contain multiple parameter cases; these 225 logical skips contain 283 skipped parameter cases.

## Interpretation

- Coverage includes test fixtures, Apache startup and shutdown, readiness checks, and the test body. A function hit does not identify which assertion caused or verified the behavior.
- `function_execution_count` is the total count for the complete isolated pytest invocation.
- The next analysis step should inspect each candidate test's inputs, exercised branches, and assertions against the mapped specification text.
- `spec_idx` is not guaranteed to be a simple row position in `http.csv`. The result files preserve both `spec_idx` and `sr_text` from `mapping.csv`.
