#!/usr/bin/env python3
"""Run verified end-to-end benchmarks and retain raw logs; no external packages."""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time


def positive(value):
    value = int(value)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def measured_seconds(text, name):
    matches = re.findall(rf"^{re.escape(name)} time: ([0-9.eE+\-]+)$", text, re.MULTILINE)
    if not matches:
        raise RuntimeError(f"missing {name} timing")
    return float(matches[-1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=Path("build-rtx4060/zkp_bench"))
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--gpu-memory-mib", type=positive, default=256)
    parser.add_argument("--bits", type=positive, default=1024)
    parser.add_argument("--messages", type=positive, nargs="+", default=[1, 10, 40, 100])
    parser.add_argument("--range-proofs", type=positive, default=128)
    parser.add_argument("--threads", type=positive, default=16)
    parser.add_argument("--repeat", type=positive, default=3)
    parser.add_argument("--modes", choices=["cpu", "cuda", "hybrid"], nargs="+",
                        default=["cpu", "cuda", "hybrid"])
    parser.add_argument("--output", type=Path, default=Path("benchmark-results"))
    args = parser.parse_args()
    if args.device < 0 or args.gpu_memory_mib < 8:
        parser.error("device must be nonnegative and GPU memory budget must be >= 8 MiB")
    binary = args.binary.resolve(strict=True)
    if "cpu" not in args.modes:
        raise ValueError("include cpu to calculate a speedup baseline")
    args.output.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env["ZKP_GPU_VERIFY"] = "0"  # Correctness testing is a separate prerequisite.
    env["ZKP_ACCEL_VERBOSE"] = "1"
    metadata = {key: str(value) if isinstance(value, Path) else value
                for key, value in vars(args).items()}
    metadata.update({"started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "gpu_result_recomputation": False,
                     "parameters": "new random key parameters in each process"})
    (args.output / "configuration.json").write_text(json.dumps(metadata, indent=2) + "\n")
    rows = []
    columns = ["bits", "messages", "mode", "run", "wall_seconds", "commit_seconds",
               "encrypt_seconds", "gpu_tasks", "gpu_kernel_ms", "log"]
    with (args.output / "runs.csv").open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        for messages in args.messages:
            for mode in dict.fromkeys(args.modes):
                for run in range(1, args.repeat + 1):
                    name = f"{args.bits}-{messages}-{mode}-{run}.log"
                    command = [str(binary), "--backend", mode, "--bits", str(args.bits),
                               "--messages", str(messages), "--range-proofs", str(args.range_proofs),
                               "--threads", str(args.threads), "--repeat", "1",
                               "--device", str(args.device), "--gpu-memory-mib", str(args.gpu_memory_mib)]
                    print(f"Running {mode}: bits={args.bits}, messages={messages}, run={run}", flush=True)
                    completed = subprocess.run(command, env=env, text=True,
                                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                    log = completed.stdout
                    (args.output / name).write_text(log)
                    wall = re.search(r"VERIFICATION=PASS run=1 WALL_SECONDS=([0-9.eE+\-]+)", log)
                    if completed.returncode != 0 or wall is None:
                        raise RuntimeError(f"benchmark/verification failed: {name}")
                    gpu_tasks = sum(map(int, re.findall(r"\bgpu=(\d+)", log)))
                    if mode != "cpu" and gpu_tasks == 0:
                        raise RuntimeError(f"no GPU work was completed; refusing GPU timing: {name}")
                    if mode != "cpu" and re.search(r"^\[(?:Commit|Encrypt)\].* reason=", log, re.MULTILINE):
                        raise RuntimeError(f"GPU fallback occurred; refusing mixed failure timing: {name}")
                    row = dict(bits=args.bits, messages=messages, mode=mode, run=run,
                               wall_seconds=float(wall.group(1)),
                               commit_seconds=measured_seconds(log, "P.commit"),
                               encrypt_seconds=measured_seconds(log, "P.encrypt"),
                               gpu_tasks=gpu_tasks,
                               gpu_kernel_ms=sum(map(float, re.findall(r"\bkernel_ms=([0-9.eE+\-]+)", log))),
                               log=name)
                    rows.append(row)
                    writer.writerow(row)
                    output.flush()
    with (args.output / "summary.csv").open("w", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["bits", "messages", "mode", "median_wall_seconds", "wall_speedup_vs_cpu",
                         "median_commit_seconds", "commit_speedup_vs_cpu"])
        for messages in args.messages:
            baseline = [r for r in rows if r["messages"] == messages and r["mode"] == "cpu"]
            base_wall = statistics.median(r["wall_seconds"] for r in baseline)
            base_commit = statistics.median(r["commit_seconds"] for r in baseline)
            for mode in dict.fromkeys(args.modes):
                group = [r for r in rows if r["messages"] == messages and r["mode"] == mode]
                wall = statistics.median(r["wall_seconds"] for r in group)
                commit = statistics.median(r["commit_seconds"] for r in group)
                writer.writerow([args.bits, messages, mode, wall, base_wall / wall if wall else "",
                                 commit, base_commit / commit if commit else ""])
    print(f"Saved {args.output / 'summary.csv'}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
