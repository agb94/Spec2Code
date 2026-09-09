#!/usr/bin/env python3
"""Collect per-pytest LLVM coverage for functions in benchmark/mapping.csv."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time


SCRIPT_PATH = Path(__file__).resolve()
DEFAULT_REPO_ROOT = SCRIPT_PATH.parents[3]
DEFAULT_SCOPES = ["test"]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def require_file(path: Path, description: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise RuntimeError(f"{description} does not exist: {resolved}")
    return resolved


def require_directory(path: Path, description: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise RuntimeError(f"{description} does not exist: {resolved}")
    return resolved


def find_program(value: str, description: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.parent != Path(".") or candidate.is_absolute():
        return require_file(candidate, description)
    found = shutil.which(value)
    if not found:
        raise RuntimeError(f"{description} was not found on PATH: {value}")
    return Path(found).resolve()


def load_mapped_functions(
    benchmark_dir: Path,
) -> tuple[list[dict[str, str]], dict[str, dict[str, str]]]:
    mappings = read_csv(benchmark_dir / "mapping.csv")
    wanted = {row["function_id"] for row in mappings}
    functions: dict[str, dict[str, str]] = {}
    for row in read_csv(benchmark_dir / "all_func.csv"):
        function_id = row["function_id"]
        if function_id not in wanted:
            continue
        source_path = row["file_path"]
        marker = "/modules/"
        source_rel = (
            "modules/" + source_path.split(marker, 1)[1]
            if marker in source_path
            else Path(source_path).name
        )
        functions[function_id] = {
            "function_id": function_id,
            "function_name": row["function_name"],
            "source_file": source_rel,
            "start_line": row["start_line"],
            "end_line": row["end_line"],
        }
    missing = wanted - functions.keys()
    if missing:
        raise RuntimeError(f"Missing all_func.csv metadata for function IDs: {sorted(missing)}")
    return mappings, functions


def collect_nodeids(pytest: Path, scopes: list[str], httpd_src: Path) -> list[str]:
    command = [str(pytest), "--collect-only", "-q", *scopes]
    result = subprocess.run(
        command,
        cwd=httpd_src,
        text=True,
        capture_output=True,
        timeout=300,
    )
    if result.returncode not in (0, 5):
        raise RuntimeError(
            "pytest collection failed. Install all suite prerequisites before "
            "collecting a full directory.\n\n"
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        )
    nodeids = []
    for line in result.stdout.splitlines():
        candidate = line.strip()
        if candidate.startswith("test/") and "::" in candidate:
            nodeids.append(candidate)
    return nodeids


def classify_test(returncode: int, output: str) -> str:
    if returncode != 0:
        return "failed"
    skipped = re.search(r"\bskipped\b", output, re.IGNORECASE)
    passed = re.search(r"\bpassed\b", output, re.IGNORECASE)
    return "skipped" if skipped and not passed else "passed"


def export_function_counts(
    profile_files: list[Path],
    profile_dir: Path,
    functions: dict[str, dict[str, str]],
    httpd_binary: Path,
    coverage_objects: list[Path],
    llvm_profdata: Path,
    llvm_cov: Path,
) -> tuple[dict[str, int], str]:
    if not profile_files:
        return {}, "No .profraw files were produced"

    merged_profile = profile_dir / "merged.profdata"
    merge = subprocess.run(
        [
            str(llvm_profdata),
            "merge",
            "-sparse",
            "--failure-mode=all",
            *map(str, profile_files),
            "-o",
            str(merged_profile),
        ],
        text=True,
        capture_output=True,
        timeout=180,
    )
    if merge.returncode:
        return {}, f"llvm-profdata failed: {merge.stderr.strip()}"

    command = [
        str(llvm_cov),
        "export",
        str(httpd_binary),
        f"-instr-profile={merged_profile}",
    ]
    command.extend(f"-object={path}" for path in coverage_objects)
    exported = subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=300,
    )
    if exported.returncode:
        return {}, f"llvm-cov failed: {exported.stderr.strip()}"

    payload = json.loads(exported.stdout)
    functions_by_name: dict[str, list[tuple[str, int]]] = {}
    for datum in payload.get("data", []):
        for entry in datum.get("functions", []):
            raw_name = entry.get("name", "")
            normalized_name = raw_name.rsplit(":", 1)[-1]
            count = int(entry.get("count", 0))
            for filename in entry.get("filenames", []):
                functions_by_name.setdefault(normalized_name, []).append((filename, count))

    counts: dict[str, int] = {}
    for function_id, metadata in functions.items():
        matches = [
            count
            for filename, count in functions_by_name.get(metadata["function_name"], [])
            if filename.endswith(metadata["source_file"])
        ]
        if matches:
            counts[function_id] = max(matches)
    notes = "\n".join(part for part in (merge.stderr.strip(), exported.stderr.strip()) if part)
    return counts, notes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run pytest tests separately and map LLVM function hits to RFC specs."
    )
    parser.add_argument(
        "scopes",
        nargs="*",
        help="pytest paths or node IDs; defaults to the complete pytest tree",
    )
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--benchmark-dir", type=Path)
    parser.add_argument(
        "--httpd-src",
        type=Path,
        help="Instrumented httpd source/test tree; defaults to data/repos/httpd/rawcode",
    )
    parser.add_argument("--install", type=Path, required=True, help="Instrumented httpd install prefix")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pytest", default="pytest")
    parser.add_argument("--llvm-profdata", default="llvm-profdata")
    parser.add_argument("--llvm-cov", default="llvm-cov")
    parser.add_argument(
        "--coverage-object",
        action="append",
        type=Path,
        default=[],
        help="Additional instrumented binary or DSO passed to llvm-cov; may be repeated",
    )
    parser.add_argument("--timeout", type=int, default=300, help="Seconds allowed per logical test")
    parser.add_argument(
        "--group-parametrized",
        action="store_true",
        help="Run all parameter cases of one logical test in the same pytest invocation",
    )
    parser.add_argument(
        "--keep-profiles",
        action="store_true",
        help="Retain raw and merged LLVM profiles after counts are exported",
    )
    parser.add_argument("--limit", type=int, help="Collect only the first N logical tests")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = require_directory(args.repo_root, "Repository root")
    benchmark_dir = require_directory(
        args.benchmark_dir or repo_root / "data" / "benchmark",
        "Benchmark directory",
    )
    httpd_src = require_directory(
        args.httpd_src or repo_root / "data" / "repos" / "httpd" / "rawcode",
        "httpd source directory",
    )
    install = require_directory(args.install, "Instrumented install prefix")
    httpd_binary = require_file(install / "bin" / "httpd", "Instrumented httpd executable")
    pytest = find_program(args.pytest, "pytest executable")
    llvm_profdata = find_program(args.llvm_profdata, "llvm-profdata")
    llvm_cov = find_program(args.llvm_cov, "llvm-cov")

    coverage_objects = [require_file(path, "Coverage object") for path in args.coverage_object]
    default_mime = install / "modules" / "mod_mime.so"
    if default_mime.is_file() and default_mime.resolve() not in coverage_objects:
        coverage_objects.append(default_mime.resolve())

    output_dir = args.output_dir.expanduser().resolve()
    if output_dir.exists():
        raise RuntimeError(f"Output directory already exists: {output_dir}")
    profiles_root = output_dir / "profiles"
    logs_root = output_dir / "logs"
    results_root = output_dir / "results"
    profiles_root.mkdir(parents=True)
    logs_root.mkdir()
    results_root.mkdir()

    scopes = args.scopes or DEFAULT_SCOPES
    mappings, functions = load_mapped_functions(benchmark_dir)
    collected_nodeids = collect_nodeids(pytest, scopes, httpd_src)
    nodeids = collected_nodeids
    if args.group_parametrized:
        nodeids = list(dict.fromkeys(nodeid.split("[", 1)[0] for nodeid in nodeids))
    if args.limit is not None:
        nodeids = nodeids[: args.limit]
    if not nodeids:
        raise RuntimeError("No pytest node IDs were collected")

    test_runs: list[dict[str, object]] = []
    function_test_rows: list[dict[str, object]] = []
    for index, nodeid in enumerate(nodeids, start=1):
        digest = hashlib.sha1(nodeid.encode()).hexdigest()[:12]
        profile_dir = profiles_root / f"{index:04d}-{digest}"
        profile_dir.mkdir()
        environment = os.environ.copy()
        environment["PATH"] = str(pytest.parent) + os.pathsep + environment.get("PATH", "")
        environment["LLVM_PROFILE_FILE"] = str(profile_dir / "%m-%p.profraw")

        started = time.monotonic()
        try:
            run = subprocess.run(
                [str(pytest), "-q", "-rs", nodeid],
                cwd=httpd_src,
                env=environment,
                text=True,
                capture_output=True,
                timeout=args.timeout,
            )
            returncode = run.returncode
            output = run.stdout + ("\n[stderr]\n" + run.stderr if run.stderr else "")
        except subprocess.TimeoutExpired as exc:
            returncode = 124
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            output = stdout + "\n[timeout]\n" + stderr
        duration = round(time.monotonic() - started, 3)
        (logs_root / f"{index:04d}-{digest}.log").write_text(output, encoding="utf-8")

        profile_files = sorted(profile_dir.glob("*.profraw"))
        profile_file_count = len(profile_files)
        counts, coverage_note = export_function_counts(
            profile_files,
            profile_dir,
            functions,
            httpd_binary,
            coverage_objects,
            llvm_profdata,
            llvm_cov,
        )
        # Raw profiles are large because every instrumented DSO writes one.
        # Keep them only when explicitly requested or when export failed.
        if not args.keep_profiles and counts:
            shutil.rmtree(profile_dir)
        status = classify_test(returncode, output)
        test_runs.append({
            "test_nodeid": nodeid,
            "test_file": nodeid.split("::", 1)[0],
            "status": status,
            "returncode": returncode,
            "duration_seconds": duration,
            "profile_file_count": profile_file_count,
            "mapped_functions_hit": sum(value > 0 for value in counts.values()),
            "coverage_note": coverage_note,
        })
        # Fixture and teardown code may produce profiles even when the test is
        # skipped or fails. Only passing invocations are candidate-test evidence.
        if status == "passed":
            for function_id, count in counts.items():
                if count <= 0:
                    continue
                metadata = functions[function_id]
                function_test_rows.append({
                    "test_nodeid": nodeid,
                    "test_file": nodeid.split("::", 1)[0],
                    "test_status": status,
                    "function_id": function_id,
                    "function_name": metadata["function_name"],
                    "source_file": metadata["source_file"],
                    "function_execution_count": count,
                })
        write_csv(results_root / "test_runs.partial.csv", list(test_runs[0]), test_runs)
        write_csv(
            results_root / "function_test_coverage.partial.csv",
            ["test_nodeid", "test_file", "test_status", "function_id", "function_name", "source_file", "function_execution_count"],
            function_test_rows,
        )
        hits = sum(value > 0 for value in counts.values())
        print(
            f"[{index}/{len(nodeids)}] {status}: {nodeid} "
            f"({profile_file_count} profiles, {hits} mapped functions)",
            flush=True,
        )

    specs_by_function: dict[str, list[dict[str, str]]] = {}
    for mapping in mappings:
        specs_by_function.setdefault(mapping["function_id"], []).append(mapping)
    hits_by_function: dict[str, list[dict[str, object]]] = {}
    for hit in function_test_rows:
        hits_by_function.setdefault(str(hit["function_id"]), []).append(hit)

    function_summary: list[dict[str, object]] = []
    for function_id, metadata in sorted(functions.items(), key=lambda item: int(item[0])):
        hits = hits_by_function.get(function_id, [])
        function_summary.append({
            **metadata,
            "mapped_spec_rows": len(specs_by_function.get(function_id, [])),
            "candidate_test_count": len(hits),
            "executed": bool(hits),
            "candidate_tests": " | ".join(str(hit["test_nodeid"]) for hit in hits),
        })

    spec_summary: list[dict[str, object]] = []
    spec_candidates: list[dict[str, object]] = []
    for mapping in mappings:
        function_id = mapping["function_id"]
        metadata = functions[function_id]
        hits = hits_by_function.get(function_id, [])
        spec_summary.append({
            "spec_idx": mapping["spec_idx"],
            "sr_text": mapping["sr_text"],
            "function_id": function_id,
            "function_name": metadata["function_name"],
            "candidate_test_count": len(hits),
            "executed": bool(hits),
            "candidate_tests": " | ".join(str(hit["test_nodeid"]) for hit in hits),
        })
        for hit in hits:
            spec_candidates.append({
                "spec_idx": mapping["spec_idx"],
                "sr_text": mapping["sr_text"],
                "function_id": function_id,
                "function_name": metadata["function_name"],
                "test_nodeid": hit["test_nodeid"],
                "test_file": hit["test_file"],
                "test_status": hit["test_status"],
                "function_execution_count": hit["function_execution_count"],
                "evidence": "mapped function executed during isolated pytest invocation",
            })

    write_csv(results_root / "test_runs.csv", list(test_runs[0]), test_runs)
    write_csv(
        results_root / "function_test_coverage.csv",
        ["test_nodeid", "test_file", "test_status", "function_id", "function_name", "source_file", "function_execution_count"],
        function_test_rows,
    )
    write_csv(
        results_root / "function_coverage_summary.csv",
        ["function_id", "function_name", "source_file", "start_line", "end_line", "mapped_spec_rows", "candidate_test_count", "executed", "candidate_tests"],
        function_summary,
    )
    write_csv(
        results_root / "spec_test_candidates.csv",
        ["spec_idx", "sr_text", "function_id", "function_name", "test_nodeid", "test_file", "test_status", "function_execution_count", "evidence"],
        spec_candidates,
    )
    write_csv(
        results_root / "spec_coverage_summary.csv",
        ["spec_idx", "sr_text", "function_id", "function_name", "candidate_test_count", "executed", "candidate_tests"],
        spec_summary,
    )

    unique_specs = {(row["spec_idx"], row["sr_text"]) for row in mappings}
    covered_unique_specs = {
        (row["spec_idx"], row["sr_text"])
        for row in mappings
        if hits_by_function.get(row["function_id"])
    }
    summary = {
        "scopes": scopes,
        "collected_tests": len(nodeids),
        "collected_parameterized_cases": len(collected_nodeids),
        "passed_tests": sum(row["status"] == "passed" for row in test_runs),
        "failed_tests": sum(row["status"] == "failed" for row in test_runs),
        "skipped_tests": sum(row["status"] == "skipped" for row in test_runs),
        "mapped_functions": len(functions),
        "mapped_functions_executed": sum(bool(hits_by_function.get(fid)) for fid in functions),
        "mapping_rows": len(mappings),
        "mapping_rows_with_candidate_tests": sum(bool(hits_by_function.get(row["function_id"])) for row in mappings),
        "unique_specs": len(unique_specs),
        "unique_specs_with_candidate_tests": len(covered_unique_specs),
        "function_test_pairs": len(function_test_rows),
        "spec_test_candidate_rows": len(spec_candidates),
        "raw_profiles_retained": args.keep_profiles,
        "result_directory": str(results_root),
    }
    (results_root / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (results_root / "test_runs.partial.csv").unlink(missing_ok=True)
    (results_root / "function_test_coverage.partial.csv").unlink(missing_ok=True)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 1 if summary["failed_tests"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(2)
