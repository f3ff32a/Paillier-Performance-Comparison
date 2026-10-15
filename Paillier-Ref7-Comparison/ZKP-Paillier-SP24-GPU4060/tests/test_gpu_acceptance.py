"""Prevent successful CPU fallback or incomplete logs from counting as GPU acceptance."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from validate_gpu import check_protocol

GOOD = """[Encrypt] requested=hybrid cpu=5 gpu=1 fallback=0 wall_ms=5
[Commit] requested=hybrid cpu=4 gpu=1 fallback=0 wall_ms=9
VERIFICATION=PASS run=1 WALL_SECONDS=0.25
"""


class AcceptanceTests(unittest.TestCase):
    def test_real_gpu_work_accepted(self):
        check_protocol(GOOD, "hybrid")

    def test_cpu_only_is_not_gpu_success(self):
        with self.assertRaises(RuntimeError):
            check_protocol(GOOD.replace("gpu=1", "gpu=0"), "hybrid")

    def test_encrypt_and_commit_must_both_use_gpu(self):
        with self.assertRaises(RuntimeError):
            check_protocol(GOOD.replace("[Encrypt]", "[other]"), "cuda")

    def test_partial_fallback_rejected(self):
        with self.assertRaises(RuntimeError):
            check_protocol(GOOD.replace("fallback=0", "fallback=2", 1), "hybrid")

    def test_unavailable_fallback_rejected(self):
        with self.assertRaises(RuntimeError):
            check_protocol(GOOD.replace("wall_ms=9", "wall_ms=9 reason=driver error"), "hybrid")

    def test_missing_proof_rejected(self):
        with self.assertRaises(RuntimeError):
            check_protocol(GOOD.replace("VERIFICATION=PASS", "VERIFICATION=FAIL"), "cuda")

    def test_cpu_baseline_requires_only_valid_proof(self):
        check_protocol("VERIFICATION=PASS run=1 WALL_SECONDS=0.01", "cpu")


if __name__ == "__main__":
    unittest.main()
