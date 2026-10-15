#!/usr/bin/env python3
"""GeniBatch/FATE Paillier common-operator benchmark; no simulated CUDA path.

The encryption primitive is the unmodified FATE v1.8.0 raw_encrypt method
called by upstream GeniBatch experiments/test_enc_dec.py. This adapter is not
GeniBatch's end-to-end PPML compiler benchmark and is not a ZKP protocol.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import importlib.util
import json
import math
import platform
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
FATE_ROOT = BASE / "vendor" / "fate-v1.8.0"
CORE_FIELDS = ("schema", "bits", "n", "n2", "messages", "randomness", "expected")
CSV_FIELDS = (
    "dataset_id", "implementation", "backend", "mode", "modulus_bits",
    "logical_count", "ciphertext_count", "run", "warmup", "host_ms",
    "kernel_ms", "verified", "packing", "threads", "cpu_tasks", "gpu_tasks",
    "fallback_tasks", "actual_n_bits", "actual_n2_bits", "compute_ms", "timing_scope",
)


class BenchmarkError(Exception):
    pass


def decimal(value: object, field: str) -> int:
    if not isinstance(value, str) or not value or not value.isascii() or not value.isdecimal():
        raise BenchmarkError(f"{field} must be a nonnegative decimal string")
    if len(value) > 1 and value.startswith("0"):
        raise BenchmarkError(f"{field} has noncanonical leading zero")
    return int(value, 10)


def load_dataset(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != "paillier-comparison-v1":
        raise BenchmarkError("Unsupported dataset schema; expected paillier-comparison-v1")
    if any(field not in data for field in CORE_FIELDS):
        raise BenchmarkError("Dataset lacks one of the seven canonical fields")
    if type(data["bits"]) is not int or not 64 <= data["bits"] <= 4096:
        raise BenchmarkError("bits must be a modulus bit length in [64,4096]")
    n = decimal(data["n"], "n")
    n2 = decimal(data["n2"], "n2")
    if n < 3 or n % 2 == 0 or n.bit_length() != data["bits"] or n2 != n * n:
        raise BenchmarkError("n must be odd, bits must equal n.bit_length(), and n2 must equal n*n")
    for field in ("messages", "randomness", "expected"):
        if not isinstance(data[field], list):
            raise BenchmarkError(f"{field} must be an array")
    if not 1 <= len(data["messages"]) <= 100000 or len(data["messages"]) != len(data["randomness"]) or len(data["messages"]) != len(data["expected"]):
        raise BenchmarkError("messages, randomness, and expected must have equal nonzero length")
    for i, (ms, rs, es) in enumerate(zip(data["messages"], data["randomness"], data["expected"])):
        m, r, expected = decimal(ms, f"messages[{i}]"), decimal(rs, f"randomness[{i}]"), decimal(es, f"expected[{i}]")
        if not 0 <= m < n or not 1 <= r < n or math.gcd(r, n) != 1:
            raise BenchmarkError(f"Invalid Paillier input at index {i}: require 0<=m<n, 1<=r<n, gcd(r,n)=1")
        oracle = ((1 + n * m) * pow(r, n, n2)) % n2
        if not 0 <= expected < n2 or expected != oracle:
            raise BenchmarkError(f"Dataset expected ciphertext is incorrect at index {i}")
    canonical = json.dumps({field: data[field] for field in CORE_FIELDS}, sort_keys=True,
                           separators=(",", ":"), ensure_ascii=True).encode("ascii")
    dataset_id = hashlib.sha256(canonical).hexdigest()
    if data.get("dataset_id") != dataset_id:
        raise BenchmarkError("dataset_id differs from SHA256 of the canonical seven fields")
    return data


def load_cpu():
    sys.path.insert(0, str(FATE_ROOT))
    try:
        import gmpy2
        from federatedml.secureprotol import fate_paillier, gmpy_math, fixedpoint
    except ImportError as exc:
        raise BenchmarkError(
            "The CPU baseline requires numpy and gmpy2. Install requirements.txt in a venv. "
            "No Python-pow replacement is enabled. Original import error: " + str(exc)
        ) from exc
    manifest = json.loads((FATE_ROOT / "SOURCE.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        path = FATE_ROOT / item["path"].removeprefix("python/")
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise BenchmarkError("Vendored FATE source differs from pinned upstream: " + str(path))
    for module in (fate_paillier, gmpy_math, fixedpoint):
        expected_path = FATE_ROOT / "federatedml" / "secureprotol" / (module.__name__.rsplit(".", 1)[-1] + ".py")
        if Path(module.__file__).resolve() != expected_path.resolve():
            raise BenchmarkError("A different FATE module is already loaded; run this adapter in a fresh process")
    return fate_paillier.PaillierPublicKey, gmpy2


def reject_unavailable_cuda():
    module_name = "federatedml.FATE_Engine.python.bigintengine.gpu.gpu_store"
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise BenchmarkError(
            "CUDA baseline unavailable: the public GeniBatch repository does not include "
            "HAFLO/FATE_Engine GPU sources, build files, or " + module_name + ". "
            "Supply the authors' genuine GPU implementation before claiming GPU results. "
            "No CPU fallback or user-project CUDA replacement is run. Import detail: " + str(exc)
        ) from exc
    if not hasattr(module, "FPN_store") or not hasattr(module, "PEN_store"):
        raise BenchmarkError("Installed GPU module lacks upstream FPN_store/PEN_store")
    raise BenchmarkError(
        "A GPU module is installed, but the published FPN_store.encrypt(pub_key).obfuscation() "
        "API does not document external per-element randomness or synchronized ciphertext "
        "export. A verified fixed-r bridge is required for this deterministic comparison. "
        "This adapter refuses to silently change the workload."
    )


def load_encoder():
    manifest = json.loads((BASE / "UPSTREAM_SOURCE.json").read_text(encoding="utf-8"))
    encoder_item = next(item for item in manifest["files"] if item["path"] == "encoding/encoder.py")
    encoder_path = BASE / "upstream" / "encoding" / "encoder.py"
    if hashlib.sha256(encoder_path.read_bytes()).hexdigest() != encoder_item["sha256"]:
        raise BenchmarkError("BatchEncoder source differs from pinned original GeniBatch")
    spec = importlib.util.spec_from_file_location("genibatch_original_encoder", BASE / "upstream" / "encoding" / "encoder.py")
    if spec is None or spec.loader is None:
        raise BenchmarkError("Cannot load unmodified upstream BatchEncoder")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.BatchEncoder


def prepare_packing(data: dict, args: argparse.Namespace):
    """Validate a distinct integer-slot demonstration outside measured work.

    It calls BatchEncoder.squeeze directly, avoiding the upstream float
    quantization step. No floating point values are substituted for integers.
    """
    data_bits, sign_bits, slot_bits = args.data_bits, args.sign_bits, args.slot_bits
    if data_bits < 2 or sign_bits < 1 or slot_bits < data_bits + sign_bits:
        raise BenchmarkError("Require data_bits>=2, sign_bits>=1, slot_bits>=data_bits+sign_bits")
    capacity = (data["bits"] - 1) // slot_bits
    size = args.pack_size if args.pack_size is not None else capacity
    if size < 1 or size > capacity:
        raise BenchmarkError(f"pack_size must be in [1,{capacity}] for the selected modulus/slots")
    messages = [int(v) for v in data["messages"]]
    if any(v >= (1 << (data_bits - 1)) for v in messages):
        raise BenchmarkError("packed-encryption requires small nonnegative messages below 2^(data_bits-1); full-range messages cannot be packed fairly")
    groups, expected = [], []
    n, n2 = int(data["n"]), int(data["n2"])
    for start in range(0, len(messages), size):
        row = messages[start:start + size]
        row += [0] * (size - len(row))
        value = 0
        for item in row:
            value = (value << slot_bits) | item
        if value >= n:
            raise BenchmarkError("Packed plaintext does not fit in the Paillier modulus")
        groups.append(list(range(start, min(start + size, len(messages)))))
        expected.append(str(((1 + n * value) * pow(int(data["randomness"][start]), n, n2)) % n2))
    return {"size": size, "groups": groups, "expected": expected, "Encoder": load_encoder()}


def run_cpu_once(data: dict, PublicKey, packed: dict | None, args: argparse.Namespace):
    # One host interval includes conversion, public-key setup, packing if enabled,
    # encryption, result allocation, and ciphertext decimal conversion.
    start = time.perf_counter_ns()
    n = int(data["n"])
    n2 = int(data["n2"])
    public_key = PublicKey(n)
    if public_key.nsquare != n2:
        raise BenchmarkError("Public-key modulus square disagrees with dataset")
    messages = [int(v) for v in data["messages"]]
    randomness = [int(v) for v in data["randomness"]]
    if packed is not None:
        size = packed["size"]
        encoder = packed["Encoder"](1 << (args.data_bits - 1), args.data_bits,
                                    args.slot_bits, args.sign_bits, size)
        grouped = [messages[start:start + size] + [0] * (size - len(messages[start:start + size]))
                   for start in range(0, len(messages), size)]
        messages = [encoder.squeeze(row) for row in grouped]
        randomness = [randomness[start] for start in range(0, len(randomness), size)]
    compute_start = time.perf_counter_ns()
    ciphertexts = [public_key.raw_encrypt(m, random_value=r)
                   for m, r in zip(messages, randomness)]
    compute_stop = time.perf_counter_ns()
    output = [str(c) for c in ciphertexts]
    # Match the other adapter's per-run public session lifecycle: keep only
    # the decimal output alive beyond this host interval.
    del n, n2, public_key, messages, randomness, ciphertexts
    if packed is not None:
        del encoder, grouped
    stop = time.perf_counter_ns()
    return output, (stop - start) / 1e6, (compute_stop - compute_start) / 1e6


def write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Each invocation produces one independent result file; no accidental mixing
    # of different schemas, runs, packing modes, or machines.
    with path.open("x", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--backend", choices=("cpu", "cuda"), default="cpu")
    p.add_argument("--repeat", type=int, default=5)
    p.add_argument("--warmup", type=int, default=1)
    p.add_argument("--output", type=Path, required=True, help="New CSV output; existing paths are rejected")
    p.add_argument("--results-output", type=Path, help="JSON ciphertexts/metadata; default: output with .json suffix")
    p.add_argument("--mode", choices=("encryption", "packed-encryption"), default="encryption")
    p.add_argument("--data-bits", type=int, default=24)
    p.add_argument("--sign-bits", type=int, default=40)
    p.add_argument("--slot-bits", type=int, default=168)
    p.add_argument("--pack-size", type=int)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    results_output = args.results_output or args.output.with_suffix(".json")
    data = None
    try:
        if args.repeat < 1 or args.warmup < 0:
            raise BenchmarkError("repeat must be >=1; warmup must be >=0")
        dataset_path, csv_path, json_path = args.dataset.resolve(), args.output.resolve(), results_output.resolve()
        if len({dataset_path, csv_path, json_path}) != 3:
            raise BenchmarkError("Dataset, CSV output and JSON results-output must be three different paths")
        if args.output.exists() or results_output.exists():
            raise BenchmarkError("Output paths must be new; existing CSV or JSON output is not overwritten")
        data = load_dataset(args.dataset)
        if args.backend == "cuda":
            reject_unavailable_cuda()
        PublicKey, gmpy2 = load_cpu()
        packed = prepare_packing(data, args) if args.mode == "packed-encryption" else None
        expected = packed["expected"] if packed else data["expected"]
        rows, final_ciphertexts = [], []
        for is_warmup, count in ((True, args.warmup), (False, args.repeat)):
            for run in range(1, count + 1):
                ciphertexts, host_ms, compute_ms = run_cpu_once(data, PublicKey, packed, args)
                if ciphertexts != expected:
                    mismatch = next((i for i, (a, b) in enumerate(zip(ciphertexts, expected)) if a != b), None)
                    raise BenchmarkError(f"Ciphertext verification failed at index {mismatch}")
                final_ciphertexts = ciphertexts
                rows.append({
                    "dataset_id": data["dataset_id"], "implementation": "genibatch-fate-cpu",
                    "backend": "cpu", "mode": args.mode, "modulus_bits": data["bits"],
                    "logical_count": len(data["messages"]), "ciphertext_count": len(ciphertexts),
                    "run": run, "warmup": int(is_warmup), "host_ms": format(host_ms, ".9f"),
                    "kernel_ms": "", "verified": 1, "packing": "genibatch-integer-slots" if packed else "none",
                    "threads": 1, "cpu_tasks": len(ciphertexts), "gpu_tasks": 0, "fallback_tasks": 0,
                    "actual_n_bits": int(data["n"]).bit_length(), "actual_n2_bits": int(data["n2"]).bit_length(),
                    "compute_ms": format(compute_ms, ".9f"),
                    "timing_scope": "host-inclusive-session-per-run",
                })
        result = {
            "schema": "paillier-comparison-results-v1", "status": "ok", "dataset_id": data["dataset_id"],
            "implementation": "genibatch-fate-cpu", "backend": "cpu", "mode": args.mode,
            "verified": True, "ciphertexts": final_ciphertexts, "runs": rows,
            "logical_count": len(data["messages"]), "ciphertext_count": len(final_ciphertexts),
            "packing": "genibatch-integer-slots" if packed else "none", "threads": 1,
            "actual_n_bits": int(data["n"]).bit_length(),
            "actual_n2_bits": int(data["n2"]).bit_length(),
            "timing_scope": "host-inclusive-session-per-run",
            "runtime": {"python": sys.version, "platform": platform.platform(), "gmpy2": gmpy2.version(),
                        "gmp": gmpy2.mp_version(), "numpy": importlib.import_module("numpy").__version__,
                        "fate": "v1.8.0", "fate_commit": "3ee02ea81c62d60353b2df40e141529b151d7c67"},
            "timing": {"host_ms": "per-run decimal input conversion, public-key construction, optional packing, original raw_encrypt, output allocation/conversion",
                       "compute_ms": "original raw_encrypt loop only; may use the upstream inverse shortcut for large plaintexts",
                       "kernel_ms": None, "excluded": ["file IO", "key generation", "randomness generation", "oracle construction", "correctness comparison", "module import"]},
            "scope": "Paillier encryption primitive shared with GeniBatch; not end-to-end PPML, not ZKP prove/verify",
            "arithmetic": "Original FATE gmpy_math: Python pow below 2^64, otherwise gmpy2.powmod; no substitute implementation",
        }
        if packed:
            result["packed_details"] = {"pack_size": packed["size"], "data_bits": args.data_bits,
                                        "slot_bits": args.slot_bits, "sign_bits": args.sign_bits,
                                        "groups": packed["groups"], "randomness_policy": "first logical input r in each group",
                                        "padding": "trailing zero slots to full pack_size; groups list only original logical slots",
                                        "quantization": "none; original BatchEncoder.squeeze on nonnegative integer slots",
                                        "comparability": "different ciphertext count and randomness count; never combine with common-operator encryption speedup"}
        write_csv(args.output, rows)
        write_json(results_output, result)
        print(f"Verified {len(final_ciphertexts)} ciphertexts per run; wrote {args.output} and {results_output}")
        return 0
    except (BenchmarkError, ValueError, OSError, KeyError) as exc:
        result = {"schema": "paillier-comparison-results-v1", "status": "error", "verified": False,
                  "backend": args.backend, "mode": args.mode, "message": str(exc)}
        if data:
            result["dataset_id"] = data["dataset_id"]
        try:
            if results_output.resolve() not in (args.dataset.resolve(), args.output.resolve()) and not results_output.exists():
                write_json(results_output, result)
        except OSError:
            pass
        print("ERROR: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
