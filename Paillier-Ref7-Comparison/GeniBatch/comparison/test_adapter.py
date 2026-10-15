"""Focused checks of the comparison contract and preserved upstream methods."""
import argparse
import copy
import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import benchmark_adapter as adapter
from shared.fixture import generate, fingerprint


class AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.PublicKey, cls.gmpy2 = adapter.load_cpu()
        cls.small = generate(64, 10, "full-domain")
        n = int(cls.small["n"])
        cls.small["messages"] = [str(v) for v in (0, 1, 2, n // 3, 2 * n // 3, n - 2, n - 1)]
        cls.small["randomness"] = [str(v) for v in (1, n - 1, 1, n - 1, 1, n - 1, 1)]
        cls.small["expected"] = [str((1 + n * int(m)) * pow(int(r), n, n*n) % (n*n))
                                 for m, r in zip(cls.small["messages"], cls.small["randomness"])]
        cls.small["dataset_id"] = fingerprint(cls.small)
        cls.packing_data = generate(512, 9, "small-integers")

    def file_for(self, data, folder):
        path = Path(folder) / "fixture.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_cpu_matches_independent_oracle_including_fate_inverse_shortcut(self):
        with tempfile.TemporaryDirectory() as folder:
            data = adapter.load_dataset(self.file_for(self.small, folder))
            out, host, compute = adapter.run_cpu_once(data, self.PublicKey, None, argparse.Namespace())
            self.assertEqual(out, data["expected"])
            self.assertGreaterEqual(host, compute)

    def test_cli_exports_all_runs_and_canonical_results(self):
        with tempfile.TemporaryDirectory() as folder:
            dataset = self.file_for(self.small, folder)
            out, results = Path(folder) / "run.csv", Path(folder) / "results.json"
            status = adapter.main(["--dataset", str(dataset), "--backend", "cpu", "--repeat", "2", "--warmup", "1",
                                   "--output", str(out), "--results-output", str(results)])
            self.assertEqual(status, 0)
            with out.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual([r["warmup"] for r in rows], ["1", "0", "0"])
            self.assertEqual([r["run"] for r in rows], ["1", "1", "2"])
            self.assertTrue(all(r["threads"] == "1" and r["gpu_tasks"] == "0" and r["fallback_tasks"] == "0" for r in rows))
            self.assertTrue(all(r["kernel_ms"] == "" for r in rows))
            result = json.loads(results.read_text(encoding="utf-8"))
            self.assertEqual(result["ciphertexts"], self.small["expected"])
            self.assertEqual(result["dataset_id"], self.small["dataset_id"])

    def test_altered_expected_rejected_even_with_recomputed_hash(self):
        bad = copy.deepcopy(self.small)
        bad["expected"][0] = str((int(bad["expected"][0]) + 1) % int(bad["n2"]))
        bad["dataset_id"] = fingerprint(bad)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(adapter.BenchmarkError, "incorrect"):
                adapter.load_dataset(self.file_for(bad, folder))

    def test_hash_wrong_rejected(self):
        bad = copy.deepcopy(self.small)
        bad["dataset_id"] = "0" * 64
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(adapter.BenchmarkError, "dataset_id"):
                adapter.load_dataset(self.file_for(bad, folder))

    def test_noncanonical_leading_zero_rejected(self):
        bad = copy.deepcopy(self.small)
        bad["messages"][0] = "00"
        bad["dataset_id"] = fingerprint(bad)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(adapter.BenchmarkError, "leading zero"):
                adapter.load_dataset(self.file_for(bad, folder))

    def test_randomness_not_a_unit_rejected(self):
        bad = copy.deepcopy(self.small)
        n = 3 * ((1 << 62) + 1)
        bad.update(n=str(n), n2=str(n*n), bits=n.bit_length(), messages=["0"], randomness=["3"],
                   expected=[str(pow(3, n, n*n))])
        bad["dataset_id"] = fingerprint(bad)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(adapter.BenchmarkError, "Invalid Paillier input"):
                adapter.load_dataset(self.file_for(bad, folder))

    def test_output_never_overwrites_dataset(self):
        with tempfile.TemporaryDirectory() as folder:
            dataset = self.file_for(self.small, folder)
            original = dataset.read_bytes()
            status = adapter.main(["--dataset", str(dataset), "--output", str(dataset)])
            self.assertEqual(status, 2)
            self.assertEqual(dataset.read_bytes(), original)
            status = adapter.main(["--dataset", str(dataset), "--output", str(Path(folder)/"new.csv"),
                                   "--results-output", str(dataset)])
            self.assertEqual(status, 2)
            self.assertEqual(dataset.read_bytes(), original)

    def test_existing_outputs_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            dataset = self.file_for(self.small, folder)
            out, results = Path(folder)/"run.csv", Path(folder)/"results.json"
            out.write_text("original CSV", encoding="utf-8")
            results.write_text("original JSON", encoding="utf-8")
            status = adapter.main(["--dataset", str(dataset), "--output", str(out), "--results-output", str(results)])
            self.assertEqual(status, 2)
            self.assertEqual(out.read_text(encoding="utf-8"), "original CSV")
            self.assertEqual(results.read_text(encoding="utf-8"), "original JSON")

    def test_cuda_missing_dependency_never_runs_cpu(self):
        with self.assertRaisesRegex(adapter.BenchmarkError, "CUDA baseline unavailable|verified fixed-r bridge"):
            adapter.reject_unavailable_cuda()

    def test_packed_integer_slots_recover_all_logical_inputs(self):
        args = argparse.Namespace(data_bits=24, sign_bits=40, slot_bits=168, pack_size=3)
        packed = adapter.prepare_packing(self.packing_data, args)
        output, host, compute = adapter.run_cpu_once(self.packing_data, self.PublicKey, packed, args)
        self.assertEqual(len(output), 3)
        self.assertEqual(output, packed["expected"])
        encoder = packed["Encoder"](1 << 23, 24, 168, 40, 3)
        logical = [int(v) for v in self.packing_data["messages"]]
        for start in range(0, len(logical), 3):
            row = logical[start:start+3]
            self.assertEqual(encoder.de_squeeze(encoder.squeeze(row), 3), row)

    def test_packing_full_domain_is_rejected(self):
        args = argparse.Namespace(data_bits=24, sign_bits=40, slot_bits=32, pack_size=1)
        # Invalid slot geometry must be rejected first.
        with self.assertRaises(adapter.BenchmarkError):
            adapter.prepare_packing(self.small, args)
        args.slot_bits = 64
        args.sign_bits = 1
        full_range = copy.deepcopy(self.packing_data)
        full_range["messages"][0] = str(int(full_range["n"]) - 1)
        with self.assertRaises(adapter.BenchmarkError):
            adapter.prepare_packing(full_range, args)

    def test_unmodified_vendored_fate_source_hashes(self):
        manifest = json.loads((HERE / "vendor" / "fate-v1.8.0" / "SOURCE.json").read_text(encoding="utf-8"))
        for item in manifest["files"]:
            relpath = item["path"].removeprefix("python/")
            path = HERE / "vendor" / "fate-v1.8.0" / relpath
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), item["sha256"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
