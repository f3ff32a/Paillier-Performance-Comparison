import copy
import csv
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from shared.fixture import fingerprint, generate, load, validate, write_new
from scripts.summarize import summarize
from scripts.run_comparison import check_results, check_csv


class FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = generate(64, 5, "full-domain")

    def test_boundaries_and_oracle(self):
        self.assertEqual(self.data["messages"][:3], ["0", "1", str(int(self.data["n"]) - 1)])
        self.assertEqual(validate(self.data), self.data)

    def test_detect_mutation(self):
        data = copy.deepcopy(self.data)
        data["expected"][0] = "0"
        data["dataset_id"] = fingerprint(data)
        with self.assertRaisesRegex(ValueError, "reference ciphertext mismatch"):
            validate(data)

    def test_detect_fingerprint_tampering(self):
        data = copy.deepcopy(self.data)
        data["dataset_id"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "dataset_id"):
            validate(data)

    def test_invalid_randomness_and_width(self):
        for key, value in [("randomness", "0"), ("messages", self.data["n"])]:
            data = copy.deepcopy(self.data)
            data[key][0] = value
            data["dataset_id"] = fingerprint(data)
            with self.assertRaises(ValueError):
                validate(data)
        data = copy.deepcopy(self.data)
        data["bits"] = 65
        with self.assertRaisesRegex(ValueError, "width"):
            validate(data)

    def test_writer_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "fixture.json"
            write_new(path, self.data)
            self.assertEqual(load(path), self.data)
            with self.assertRaises(FileExistsError):
                write_new(path, self.data)


class SummaryTests(unittest.TestCase):
    def row(self, **overrides):
        row = dict(dataset_id="a" * 64, implementation="genibatch-fate-cpu", backend="cpu",
                   mode="encryption", modulus_bits=64, logical_count=5, ciphertext_count=5,
                   run=1, warmup=0, host_ms=10, kernel_ms="", verified="PASS", packing="none",
                   threads=1, cpu_tasks=5, gpu_tasks=0, fallback_tasks=0,
                   timing_scope="host-inclusive-session-per-run")
        row.update(overrides)
        return row

    def aggregate(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "a.csv"
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            return summarize([path])

    def test_median_and_speedup(self):
        values = self.aggregate([self.row(host_ms=10), self.row(host_ms=20, run=2),
                                 self.row(host_ms=999, warmup=1),
                                 self.row(implementation="zkp-paillier", backend="cuda", threads=16,
                                          host_ms=3, gpu_tasks=5, cpu_tasks=0)])
        self.assertEqual(values[0]["median_host_ms"], 15)
        self.assertEqual(values[1]["speedup_vs_genibatch_fate_single_cpu"], 5)

    def test_reject_packed_and_fallback(self):
        for bad in [self.row(packing="slots"), self.row(fallback_tasks=1),
                    self.row(logical_count=10), self.row(backend="cuda"), self.row(verified="FAIL"),
                    self.row(backend="cuda", gpu_tasks=1, cpu_tasks=4),
                    self.row(cpu_tasks=4), self.row(gpu_tasks=5)]:
            with self.assertRaises(ValueError):
                self.aggregate([bad])

    def test_different_fixture_is_not_paired(self):
        values = self.aggregate([self.row(), self.row(implementation="zkp-paillier", dataset_id="b"*64)])
        self.assertNotIn("speedup_vs_genibatch_fate_single_cpu", values[1])

    def test_different_scope_is_not_paired(self):
        values = self.aggregate([self.row(), self.row(implementation="zkp-paillier", timing_scope="kernel-only")])
        self.assertNotIn("speedup_vs_genibatch_fate_single_cpu", values[1])


class RunnerGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = generate(64, 3)

    def metadata(self):
        d = self.data
        return dict(dataset_id=d["dataset_id"], ciphertexts=d["expected"], implementation="zkp-paillier",
                    backend="cpu", threads=1, mode="encryption", packing="none", verified=True,
                    actual_n_bits=64, actual_n2_bits=int(d["n2"]).bit_length(), logical_count=3,
                    ciphertext_count=3, timing_scope="host-inclusive-session-per-run")

    def test_result_metadata_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "result.json"
            good = self.metadata()
            path.write_text(json.dumps(good), encoding="utf-8")
            check_results(path, self.data, "zkp-paillier", "cpu", 1)
            for field, invalid in [("threads", 16), ("backend", "cuda"), ("implementation", "other"),
                                   ("actual_n_bits", 63), ("verified", False)]:
                bad = {**good, field: invalid}
                path.write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(ValueError):
                    check_results(path, self.data, "zkp-paillier", "cpu", 1)

    def test_csv_parameter_guards(self):
        row = SummaryTests().row(dataset_id=self.data["dataset_id"], implementation="zkp-paillier",
                                 logical_count=3, ciphertext_count=3, cpu_tasks=3,
                                 actual_n_bits=64, actual_n2_bits=int(self.data["n2"]).bit_length())
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "run.csv"
            def write(value):
                with path.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=list(value))
                    writer.writeheader(); writer.writerow(value)
            write(row)
            check_csv(path, self.data, "zkp-paillier", "cpu", 1, 1, 0)
            for field, invalid in [("threads", 4), ("actual_n2_bits", 64), ("run", 2)]:
                write({**row, field: invalid})
                with self.assertRaises(ValueError):
                    check_csv(path, self.data, "zkp-paillier", "cpu", 1, 1, 0)


if __name__ == "__main__":
    unittest.main()
