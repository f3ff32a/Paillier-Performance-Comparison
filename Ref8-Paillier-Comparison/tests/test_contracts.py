"""Acceptance contracts: invalid operands/results must never yield speedups."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import compare
import fixtures

class Contracts(unittest.TestCase):
    def record(self,backend="user_cpu"):
        return {"backend":backend,"count":3,"n_bits":7,"repeat":0,"verified":True,
            "wall_seconds":0.1,"kernel_ms":0.0,"cpu_tasks":3,"gpu_tasks":0,"fallback_tasks":0}

    def test_general_paillier_agreement_and_decryption(self):
        n,p,q=77,7,11
        phi=(p-1)*(q-1)
        for m in (0,1,n//2,n-1):
            for r in (1,2,n-1):
                c=fixtures.encrypt(n,m,r)
                general=pow(n+1,m,n*n)*pow(r,n,n*n)%(n*n)
                self.assertEqual(c,general)
                self.assertEqual(((pow(c,phi,n*n)-1)//n * pow(phi,-1,n))%n,m)

    def test_generated_key_and_same_seed(self):
        first=fixtures.make_case(64,4,"edges",1,42)
        second=fixtures.make_case(64,4,"edges",1,42)
        self.assertEqual(first,second)
        n,pairs,meta=first
        self.assertIn(n.bit_length(),(63,64))
        self.assertTrue(meta["key_checked_by_decryption"])
        self.assertEqual([m for m,r in pairs],[0,1,n-1,n//2])

    def test_fixture_domain_rejections(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"f.txt"
            for content in ("", "78\n1 2\n", "77\n77 2\n", "77\n0 0\n", "77\n0 7\n", "77\n-1 2\n", "77\n1 2 extra\n", "77\n"):
                p.write_text(content)
                with self.assertRaises(ValueError): fixtures.read_case(p)

    def test_fixture_no_overwrite_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"f.txt"
            meta=fixtures.write_case(p,77,[(0,2),(76,3)],{})
            self.assertEqual(compare.digest(p),meta["sha256"])
            self.assertEqual(fixtures.read_case(p),(77,[(0,2),(76,3)]))
            with self.assertRaises(FileExistsError): fixtures.write_case(p,77,[(1,2)],{})

    def test_valid_cpu_and_cuda_records(self):
        compare.validate_records([self.record()],"user_cpu",77,3,1)
        r=self.record("ref8_cuda")
        r.update(cpu_tasks=0,gpu_tasks=3,kernel_ms=2)
        compare.validate_records([r],"ref8_cuda",77,3,1)

    def test_invalid_gpu_and_bad_timings_rejected(self):
        base=self.record("ref8_cuda")
        base.update(cpu_tasks=0,gpu_tasks=3)
        for edit in ({"verified":False},{"verified":"true"},{"gpu_tasks":0},
                     {"fallback_tasks":1},{"count":2},{"n_bits":8},
                     {"backend":"user_cuda"},{"wall_seconds":0},
                     {"wall_seconds":float("nan")},{"kernel_ms":float("inf")},
                     {"kernel_ms":-1},{"repeat":-1},{"repeat":False},
                     {"cpu_tasks":1},{"cpu_tasks":False},{"gpu_tasks":True},{"fallback_tasks":False}):
            r=dict(base,**edit)
            with self.assertRaises(ValueError): compare.validate_records([r],"ref8_cuda",77,3,1)
        del base["fallback_tasks"]
        with self.assertRaises(ValueError): compare.validate_records([base],"ref8_cuda",77,3,1)

    def test_missing_or_duplicate_repeats_rejected(self):
        with self.assertRaises(ValueError): compare.validate_records([],"user_cpu",77,3,1)
        with self.assertRaises(ValueError): compare.validate_records([self.record(),self.record()],"user_cpu",77,3,2)

    def test_corrupt_output_rejected(self):
        pairs=[(0,2),(76,3)]
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"c.txt"
            correct=[fixtures.encrypt(77,m,r) for m,r in pairs]
            p.write_text("\n".join(map(str,correct))+"\n")
            compare.validate_output(p,77,pairs)
            for content in ("1\n1\n", str(correct[0])+"\n", "-1\n1\n", "5929\n1\n"):
                p.write_text(content)
                with self.assertRaises(ValueError): compare.validate_output(p,77,pairs)

    def test_summary_uses_same_case_medians(self):
        rows=[]
        for backend,times in (("user_cpu",(10.,14.,12.)),("ref8_cuda",(4.,8.,6.)),("user_cuda",(2.,4.,3.))):
            for t in times:
                rows.append(dict(self.record(backend),wall_seconds=t,kernel_ms=t*100,
                    requested_n_bits=512,dataset="structured",fixture_sha256="same"))
        summary={r["backend"]:r for r in compare.summarize(rows)}
        self.assertEqual(summary["user_cuda"]["median_wall_seconds"],3.)
        self.assertEqual(summary["user_cuda"]["speedup_vs_user_cpu"],4.)
        self.assertEqual(summary["user_cuda"]["speedup_vs_ref8_cuda"],2.)

if __name__=="__main__":
    unittest.main(verbosity=2)
