#!/usr/bin/env python3
"""Aggregate verified shared-operator runs; refuse incomparable rows."""
import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path


def summarize(paths):
    groups = defaultdict(list)
    for path in paths:
        with Path(path).open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                if row["warmup"] not in ("0", "false", "False"):
                    continue
                if row["verified"].lower() not in ("1", "true", "pass"):
                    raise ValueError(f"unverified row in {path}")
                if row["packing"] != "none" or row["mode"] != "encryption":
                    raise ValueError("packed/ML/ZKP data cannot be used for shared-operator speedups")
                if int(row["logical_count"]) != int(row["ciphertext_count"]):
                    raise ValueError("unequal logical and ciphertext counts")
                if int(row.get("fallback_tasks", 0)) != 0:
                    raise ValueError("fallback result cannot enter comparison")
                count = int(row["ciphertext_count"])
                cpu_tasks, gpu_tasks = int(row["cpu_tasks"]), int(row["gpu_tasks"])
                if cpu_tasks < 0 or gpu_tasks < 0 or cpu_tasks + gpu_tasks != count:
                    raise ValueError("completed task counts do not match ciphertext count")
                if row["backend"] in ("cuda", "hybrid") and gpu_tasks == 0:
                    raise ValueError("GPU result has zero GPU tasks")
                if row["backend"] == "cuda" and gpu_tasks != count:
                    raise ValueError("CUDA result contains CPU tasks")
                if row["backend"] == "cpu" and gpu_tasks != 0:
                    raise ValueError("CPU result contains GPU tasks")
                host = float(row["host_ms"])
                if not math.isfinite(host) or host <= 0:
                    raise ValueError("invalid host wall time")
                key = (row["dataset_id"], row["implementation"], row["backend"],
                       row["modulus_bits"], row["ciphertext_count"], row["threads"],
                       row["timing_scope"])
                groups[key].append(host)
    result = []
    for key, samples in groups.items():
        did, impl, backend, bits, count, threads, scope = key
        median = statistics.median(samples)
        result.append(dict(dataset_id=did, implementation=impl, backend=backend,
                           modulus_bits=int(bits), ciphertext_count=int(count), threads=int(threads),
                           timing_scope=scope, samples=len(samples), median_host_ms=median,
                           min_host_ms=min(samples), max_host_ms=max(samples),
                           ciphertexts_per_second=int(count) * 1000 / median))
    # Pair only matching dataset, count and host timing scope. Baseline has exactly one CPU worker.
    for row in result:
        baselines = [b for b in result if b["dataset_id"] == row["dataset_id"]
                     and b["ciphertext_count"] == row["ciphertext_count"]
                     and b["modulus_bits"] == row["modulus_bits"]
                     and b["timing_scope"] == row["timing_scope"]
                     and b["implementation"].startswith("genibatch")
                     and b["backend"] == "cpu" and b["threads"] == 1]
        if len(baselines) == 1:
            row["speedup_vs_genibatch_fate_single_cpu"] = baselines[0]["median_host_ms"] / row["median_host_ms"]
        own_cpu = [b for b in result if b["dataset_id"] == row["dataset_id"]
                   and b["ciphertext_count"] == row["ciphertext_count"]
                   and b["modulus_bits"] == row["modulus_bits"]
                   and b["timing_scope"] == row["timing_scope"]
                   and b["implementation"] == "zkp-paillier" and b["backend"] == "cpu"]
        for baseline in own_cpu:
            row[f"speedup_vs_zkp_cpu_{baseline['threads']}_threads"] = baseline["median_host_ms"] / row["median_host_ms"]
    if not result:
        raise ValueError("no verified measured samples")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = summarize(args.csv)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump({"comparison": "Paillier shared encryption operator only", "rows": rows},
                  stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
