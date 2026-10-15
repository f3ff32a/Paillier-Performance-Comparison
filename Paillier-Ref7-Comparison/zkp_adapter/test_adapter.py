#!/usr/bin/env python3
"""Runtime contract tests for the compiled ordinary-encryption adapter (CPU by default)."""
import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


def fixture():
    n = 17 * 19
    values = [0, 1, 2, 17, n - 1]
    randomness = [1, 2, 3, 5, n - 1]
    core = {"schema": "paillier-comparison-v1", "bits": n.bit_length(), "n": str(n),
            "n2": str(n * n), "messages": list(map(str, values)),
            "randomness": list(map(str, randomness)),
            "expected": [str((1 + n * m) * pow(r, n, n * n) % (n * n))
                         for m, r in zip(values, randomness)]}
    core["dataset_id"] = hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":"),
                                                   ensure_ascii=True).encode("ascii")).hexdigest()
    return core


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--gpu", action="store_true", help="also require CUDA and hybrid correctness")
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    passed = []
    with tempfile.TemporaryDirectory(prefix="paillier-adapter-contract-") as temporary:
        root = Path(temporary)
        good = fixture()
        dataset = root / "good.json"
        dataset.write_text(json.dumps(good), encoding="utf-8")

        def call(name, values, expect_success):
            run = subprocess.run([str(binary)] + values, text=True, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, timeout=120)
            if (run.returncode == 0) != expect_success:
                raise AssertionError(f"{name}: unexpected status {run.returncode}\n{run.stdout}")
            if not expect_success and (run.returncode != 1 or "ERROR:" not in run.stdout):
                raise AssertionError(f"{name}: rejection must be a caught error, not a crash\n{run.stdout}")
            passed.append(name)
            return run.stdout

        checked = call("valid_check_only", ["--dataset", str(dataset), "--check-only"], True)
        assert "DATASET=PASS" in checked
        modes = ["cpu", "cuda", "hybrid"] if args.gpu else ["cpu"]
        for mode in modes:
            output = root / f"{mode}.csv"
            results = root / f"{mode}.json"
            logged = call(f"{mode}_correct", ["--dataset", str(dataset), "--backend", mode,
                "--threads", "2", "--warmup", "1", "--repeat", "2", "--output", str(output),
                "--results-output", str(results)], True)
            assert logged.count("VERIFICATION=PASS") == 3 and "PASS implementation=" in logged
            rows = list(csv.DictReader(output.open(encoding="utf-8", newline="")))
            assert len(rows) == 3 and [r["warmup"] for r in rows] == ["1", "0", "0"]
            for row in rows:
                assert row["dataset_id"] == good["dataset_id"] and row["verified"] == "true"
                assert row["implementation"] == "zkp-paillier" and row["packing"] == "none"
                assert int(row["cpu_tasks"]) + int(row["gpu_tasks"]) == len(good["messages"])
                assert row["fallback_tasks"] == "0" and float(row["host_ms"]) >= 0
                assert row["timing_scope"] == "host-inclusive-session-per-run"
                assert int(row["actual_n_bits"]) == int(good["n"]).bit_length()
                assert int(row["actual_n2_bits"]) == int(good["n2"]).bit_length()
                if mode == "cpu":
                    assert row["gpu_tasks"] == "0" and float(row["kernel_ms"]) == 0
                elif mode == "cuda":
                    assert int(row["gpu_tasks"]) == len(good["messages"])
                else:
                    assert int(row["gpu_tasks"]) > 0
            final = json.loads(results.read_text(encoding="utf-8"))
            assert final["ciphertexts"] == good["expected"] and final["dataset_id"] == good["dataset_id"]
            call(f"{mode}_refuse_csv_overwrite", ["--dataset", str(dataset), "--output", str(output)], False)
            call(f"{mode}_refuse_json_overwrite", ["--dataset", str(dataset), "--output", str(root / f"new-{mode}.csv"),
                 "--results-output", str(results)], False)

        mutations = [
            ("wrong_schema", lambda d: d.update(schema="wrong")),
            ("n2_mismatch", lambda d: d.update(n2="104330")),
            ("even_n", lambda d: d.update(n="322", n2=str(322 * 322))),
            ("bits_mismatch", lambda d: d.update(bits=8)),
            ("float_bits", lambda d: d.update(bits=9.0)),
            ("zero_r", lambda d: d["randomness"].__setitem__(0, "0")),
            ("r_equal_n", lambda d: d["randomness"].__setitem__(0, d["n"])),
            ("noncoprime_r", lambda d: d["randomness"].__setitem__(0, "17")),
            ("m_equal_n", lambda d: d["messages"].__setitem__(0, d["n"])),
            ("negative_m", lambda d: d["messages"].__setitem__(0, "-1")),
            ("numeric_m", lambda d: d["messages"].__setitem__(0, 0)),
            ("leading_zero_m", lambda d: d["messages"].__setitem__(0, "00")),
            ("empty_messages", lambda d: d.update(messages=[])),
            ("unequal_lengths", lambda d: d["randomness"].pop()),
            ("wrong_expected", lambda d: d["expected"].__setitem__(0, "2")),
            ("malformed_id", lambda d: d.update(dataset_id="not-sha256")),
            ("missing_expected", lambda d: d.pop("expected")),
        ]
        for name, mutate in mutations:
            d = copy.deepcopy(good)
            mutate(d)
            path = root / f"{name}.json"
            path.write_text(json.dumps(d), encoding="utf-8")
            call(name, ["--dataset", str(path), "--check-only"], False)
        trailing = root / "trailing.json"
        trailing.write_text(json.dumps(good) + " {}", encoding="utf-8")
        call("trailing_json", ["--dataset", str(trailing), "--check-only"], False)
        duplicate = root / "duplicate.json"
        duplicate.write_text(json.dumps(good)[:-1] + ', "n":"323"}', encoding="utf-8")
        call("duplicate_root_key", ["--dataset", str(duplicate), "--check-only"], False)
        call("zero_threads", ["--dataset", str(dataset), "--threads", "0", "--check-only"], False)
        call("zero_repeat", ["--dataset", str(dataset), "--repeat", "0", "--check-only"], False)
        call("unknown_option", ["--dataset", str(dataset), "--unknown", "1", "--check-only"], False)
        call("duplicate_option", ["--dataset", str(dataset), "--threads", "1", "--threads", "2", "--check-only"], False)
        if not args.gpu:
            print("GPU cases were not requested; CPU contract tests do not validate CUDA execution.")
    print(f"ADAPTER_TESTS=PASS cases={len(passed)}")
    for name in passed:
        print(f"  PASS {name}")


if __name__ == "__main__":
    main()
