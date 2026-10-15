#!/usr/bin/env python3
"""Run both real implementations on the same public ciphertext workload."""
import argparse
import csv
import datetime
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from shared.fixture import generate, load, write_new
from scripts.summarize import summarize


def execute(command, log_path, timeout):
    with log_path.open("x", encoding="utf-8") as log:
        log.write("ARGV=" + json.dumps([str(x) for x in command]) + "\n")
        log.flush()
        completed = subprocess.run([str(x) for x in command], stdout=log, stderr=subprocess.STDOUT,
                                   timeout=timeout, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    if completed.returncode:
        raise RuntimeError(f"comparison program failed ({completed.returncode}); see {log_path}")


def check_results(path, fixture, implementation, backend, threads):
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("dataset_id") != fixture["dataset_id"]:
        raise ValueError(f"result dataset_id mismatch: {path}")
    if value.get("ciphertexts") != fixture["expected"]:
        raise ValueError(f"result ciphertexts mismatch: {path}")
    required = dict(implementation=implementation, backend=backend, threads=threads,
                    mode="encryption", packing="none", verified=True,
                    actual_n_bits=fixture["bits"], actual_n2_bits=int(fixture["n2"]).bit_length(),
                    logical_count=len(fixture["messages"]), ciphertext_count=len(fixture["messages"]),
                    timing_scope="host-inclusive-session-per-run")
    if any(value.get(k) != v for k, v in required.items()):
        raise ValueError(f"result implementation/parameters mismatch: {path}")


def check_csv(path, fixture, implementation, backend, threads, repeat, warmup):
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != repeat + warmup:
        raise ValueError(f"wrong run count: {path}")
    measured = []
    for row in rows:
        if (row["dataset_id"] != fixture["dataset_id"] or row["backend"] != backend
            or row["implementation"] != implementation or int(row["threads"]) != threads
            or int(row["modulus_bits"]) != fixture["bits"]
            or int(row["actual_n_bits"]) != fixture["bits"]
            or int(row["actual_n2_bits"]) != int(fixture["n2"]).bit_length()
            or int(row["logical_count"]) != len(fixture["messages"])
            or int(row["ciphertext_count"]) != len(fixture["messages"])
            or row["mode"] != "encryption" or row["packing"] != "none"
            or row["timing_scope"] != "host-inclusive-session-per-run"):
            raise ValueError(f"incomparable CSV row: {path}")
        if row["warmup"] in ("0", "false", "False"):
            measured.append(int(row["run"]))
    if sorted(measured) != list(range(1, repeat + 1)):
        raise ValueError(f"missing or duplicated measured run: {path}")
    # Additional checks (verified, GPU participation, fallback and timings) in summarize.
    summarize([path])


def device_information():
    try:
        proc = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        return {"exit_code": proc.returncode, "output": proc.stdout.strip(), "stderr": proc.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"unavailable": str(error)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zkp-binary", type=Path, required=True,
                        help="compiled paillier_compare, not original end-to-end zkp_bench")
    parser.add_argument("--datasets", nargs="+", type=Path)
    parser.add_argument("--bits", nargs="+", type=int, default=[256, 512, 1024])
    parser.add_argument("--messages", nargs="+", type=int, default=[1, 10, 40, 100, 200, 400])
    parser.add_argument("--profile", choices=["zkp-slots", "full-domain", "small-integers"], default="zkp-slots")
    parser.add_argument("--zkp-backends", nargs="+", choices=["cpu", "cuda", "hybrid"], default=["cpu", "cuda", "hybrid"])
    parser.add_argument("--threads", type=int, default=16)
    parser.add_argument("--cpu-threads", nargs="+", type=int,
                        help="CPU budgets to test; default: 1 and --threads")
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=1800, help="timeout seconds per process")
    parser.add_argument("--output", type=Path, required=True, help="new results directory")
    args = parser.parse_args()
    if args.repeat < 1 or args.warmup < 0 or not 1 <= args.threads <= 4096 or args.timeout < 1:
        parser.error("invalid run/thread/timeout parameters")
    binary = args.zkp_binary.resolve()
    if not binary.is_file():
        parser.error(f"compile the adapter first: {binary}")
    if len(set(args.zkp_backends)) != len(args.zkp_backends):
        parser.error("duplicate backend")
    cpu_threads = sorted(set(args.cpu_threads or [1, args.threads]))
    if any(t < 1 or t > 4096 for t in cpu_threads):
        parser.error("CPU thread budgets must be in [1,4096]")
    if args.datasets:
        datasets = [(path.resolve(), load(path)) for path in args.datasets]
        if len({data["dataset_id"] for _, data in datasets}) != len(datasets):
            parser.error("duplicate dataset")
    else:
        datasets = None
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    environment = dict(created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       python=sys.version, platform=platform.platform(), cpu=platform.processor(),
                       arguments=vars(args).copy(), gpu=device_information(),
                       note="Same-input ordinary Paillier encryption. No ZKP protocol speedup inferred.")
    env_names = ["OMP_DYNAMIC", "OMP_THREAD_LIMIT", "OMP_NUM_THREADS", "OMP_PROC_BIND", "OMP_PLACES",
                 "CUDA_VISIBLE_DEVICES", "ZKP_BACKEND", "ZKP_CUDA_DEVICE", "ZKP_CPU_THREADS",
                 "ZKP_GPU_CHUNK", "ZKP_GPU_MAX_CHUNK", "ZKP_GPU_MEMORY_MIB", "ZKP_GPU_VERIFY",
                 "ZKP_ACCEL_VERBOSE"]
    environment["execution_environment"] = {name: os.environ.get(name) for name in env_names}
    environment["arguments"] = {k: str(v) if isinstance(v, Path) else [str(x) for x in v]
                                if isinstance(v, list) else v for k, v in environment["arguments"].items()}
    write_new(out / "environment.json", environment)
    if datasets is None:
        datasets = []
        for bits in args.bits:
            for count in args.messages:
                path = out / "datasets" / f"n{bits}-c{count}.json"
                data = generate(bits, count, args.profile)
                write_new(path, data)
                datasets.append((path, data))
    csv_paths = []
    try:
        for index, (dataset, fixture) in enumerate(datasets, 1):
            work = out / f"case-{index:03d}-n{fixture['bits']}-c{len(fixture['messages'])}"
            work.mkdir()
            # Preserve fixtures even when external --datasets paths were used.
            write_new(work / "dataset.json", fixture)
            fixed_dataset = work / "dataset.json"
            commands = [("genibatch-cpu", "genibatch-fate-cpu", "cpu", 1, [sys.executable,
                         ROOT / "GeniBatch" / "comparison" / "compare_paillier.py",
                         "--dataset", fixed_dataset, "--backend", "cpu"])]
            for backend in args.zkp_backends:
                for threads in cpu_threads if backend == "cpu" else [args.threads]:
                    commands.append((f"zkp-{backend}-t{threads}", "zkp-paillier", backend, threads,
                                     [binary, "--dataset", fixed_dataset, "--backend", backend,
                                      "--threads", threads]))
            for name, implementation, backend, threads, command in commands:
                csv_path, result = work / (name + ".csv"), work / (name + ".json")
                command += ["--repeat", args.repeat, "--warmup", args.warmup,
                            "--output", csv_path, "--results-output", result]
                print(f"RUN case={index} implementation={name}", flush=True)
                execute(command, work / (name + ".log"), args.timeout)
                check_results(result, fixture, implementation, backend, threads)
                check_csv(csv_path, fixture, implementation, backend, threads, args.repeat, args.warmup)
                csv_paths.append(csv_path)
        rows = summarize(csv_paths)
        write_new(out / "summary.json", {"status": "PASS", "rows": rows,
                                        "scope": "shared ordinary encryption operator"})
        print(f"COMPARISON=PASS output={out}")
    except Exception as error:
        write_new(out / "failure.json", {"status": "FAIL", "error": str(error),
                                        "completed_csv": [str(p) for p in csv_paths]})
        raise


if __name__ == "__main__":
    main()
