#!/usr/bin/env python3
"""Strict GPU acceptance: kernel, GMP oracles, transcripts, and complete proofs."""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from diagnose import collect

ROOT = Path(__file__).resolve().parents[1]


def check_protocol(log, mode):
    if "VERIFICATION=PASS run=1" not in log:
        raise RuntimeError("proof verification marker is missing")
    if mode == "cpu":
        return
    for stage in ("Encrypt", "Commit"):
        lines = re.findall(rf"^\[{stage}\].*$", log, re.MULTILINE)
        if not lines or sum(int(v) for line in lines for v in re.findall(r"\bgpu=(\d+)", line)) == 0:
            raise RuntimeError(f"{stage} did not complete actual GPU work")
        if any(" reason=" in line or re.search(r"\bfallback=[1-9]\d*", line) for line in lines):
            raise RuntimeError(f"{stage} fell back to CPU")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build-rtx4060")
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--bits", type=int, default=512, help="additional full-protocol test modulus size")
    parser.add_argument("--messages", type=int, default=10)
    parser.add_argument("--gpu-memory-mib", type=int, default=256)
    parser.add_argument("--timeout", type=int, default=1800, help="per-test seconds")
    parser.add_argument("--output", type=Path, help="new directory for logs and report.json")
    parser.add_argument("--sanitizer", action="store_true", help="also run compute-sanitizer memcheck")
    args = parser.parse_args()
    if (args.device < 0 or args.threads < 1 or args.bits < 64 or args.bits % 8 or
            args.messages < 1 or args.gpu_memory_mib < 8 or args.timeout < 1):
        parser.error("invalid device/thread/modulus/message/memory/timeout setting")
    build = args.build_dir.resolve(strict=True)
    for name in ("zkp_device_info", "acceleration_tests", "zkp_bench"):
        if not (build / name).is_file():
            raise RuntimeError(f"{name} missing: build CUDA with ZKP_BUILD_TESTS=ON first")
    sanitizer = shutil.which("compute-sanitizer") if args.sanitizer else None
    if args.sanitizer and sanitizer is None:
        raise RuntimeError("--sanitizer requested but compute-sanitizer was not found")
    output = args.output or ROOT / ("validation-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    output.mkdir(parents=True, exist_ok=False)
    report = {"project": "ZKP-Paillier-SP24-GPU2", "status": "RUNNING", "tests": [],
              "device": args.device, "bits": args.bits, "messages": args.messages}
    def save():
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    def run(name, command, env):
        logfile = output / (name + ".log")
        entry = {"name": name, "command": list(map(str, command)), "log": logfile.name, "status": "RUNNING"}
        report["tests"].append(entry)
        save()
        print(f"Running {name}; log: {logfile}", flush=True)
        with logfile.open("w") as log:
            try:
                result = subprocess.run(list(map(str, command)), stdout=log, stderr=subprocess.STDOUT,
                                        env=env, timeout=args.timeout)
            except subprocess.TimeoutExpired:
                entry["status"] = "TIMEOUT"
                raise RuntimeError(f"{name} timed out; see {logfile}")
        entry["returncode"] = result.returncode
        if result.returncode != 0:
            entry["status"] = "FAIL"
            raise RuntimeError(f"{name} failed (exit {result.returncode}); a skip is not a pass. See {logfile}")
        entry["status"] = "PROCESS_PASS"
        save()
        return logfile.read_text(), entry
    save()
    try:
        diagnostics = collect(build, device=args.device)
        (output / "environment.json").write_text(json.dumps(diagnostics, indent=2, ensure_ascii=False) + "\n")
        if not diagnostics["cuda_probe_pass"]:
            raise RuntimeError(f"real CUDA kernel probe failed; see {output / 'environment.json'}")
        env = os.environ.copy()
        env.update(ZKP_GPU_VERIFY="1", ZKP_ACCEL_VERBOSE="1", ZKP_CUDA_DEVICE=str(args.device),
                   OMP_NUM_THREADS=str(args.threads))
        text, entry = run("cuda_correctness", [build / "acceleration_tests", "--cuda", "--device", args.device], env)
        if "PASS full proof, fixed-randomness transcript equality, tamper rejection" not in text:
            entry["status"] = "FAIL"
            raise RuntimeError("correctness test did not report complete transcript verification")
        entry["status"] = "PASS"
        for bits, messages in dict.fromkeys([(64, 3), (args.bits, args.messages)]):
            for mode in ("cpu", "cuda", "hybrid"):
                command = [build / "zkp_bench", "--backend", mode, "--bits", bits, "--messages", messages,
                           "--range-proofs", "16" if bits > 64 else "3", "--threads", args.threads,
                           "--device", args.device, "--gpu-memory-mib", args.gpu_memory_mib, "--verify-gpu"]
                text, entry = run(f"proof-{bits}-{messages}-{mode}", command, env)
                try:
                    check_protocol(text, mode)
                except RuntimeError:
                    entry["status"] = "FAIL"
                    raise
                entry["status"] = "PASS"
        if sanitizer:
            _, entry = run("memcheck", [sanitizer, "--tool", "memcheck", "--error-exitcode", "99",
                              "--leak-check", "full", build / "acceleration_tests", "--cuda",
                              "--device", args.device], env)
            entry["status"] = "PASS"
        report["status"] = "PASS"
        print(f"GPU_VALIDATION=PASS\nReport: {output / 'report.json'}")
    except Exception as error:
        report["status"] = "FAIL"
        report["error"] = str(error)
        raise
    finally:
        save()


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as error:
        print(f"GPU_VALIDATION=FAIL {error}", file=sys.stderr)
        sys.exit(1)
