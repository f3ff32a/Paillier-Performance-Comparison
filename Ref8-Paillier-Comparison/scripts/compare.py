"""Run matched native Paillier operators, reject invalid results, summarize CSV."""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import math
import platform
import statistics
import subprocess
import sys
import time
from fixtures import make_case, write_case, read_case, encrypt

BACKENDS=("user_cpu","user_cuda","user_hybrid","ref8_cuda")
ROOT=Path(__file__).resolve().parents[1]

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def binary(build,name):
    for directory in (build,build/"Release"):
        for suffix in ("", ".exe"):
            p=directory/(name+suffix)
            if p.is_file():
                return p.resolve()
    raise FileNotFoundError(f"binary {name} missing in {build}; build CMake first")

def validate_records(records,expected_backend,n,count,repeats):
    if len(records)!=repeats:
        raise ValueError(f"expected {repeats} timing rows, got {len(records)}")
    seen=set()
    for row in records:
        if row.get("backend")!=expected_backend or type(row.get("count")) is not int or row.get("count")!=count or type(row.get("n_bits")) is not int or row.get("n_bits")!=n.bit_length():
            raise ValueError("backend/count/modulus metadata mismatch")
        if row.get("verified") is not True:
            raise ValueError("native every-repeat verification is required")
        rep=row.get("repeat")
        if type(rep) is not int or rep in seen or not 0<=rep<repeats:
            raise ValueError("invalid repeat index")
        seen.add(rep)
        for field in ("wall_seconds","kernel_ms"):
            value=row.get(field)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
                raise ValueError(f"invalid timing {field}")
        if row["wall_seconds"]<=0:
            raise ValueError("nonpositive operator wall time")
        if type(row.get("fallback_tasks")) is not int or row["fallback_tasks"]!=0:
            raise ValueError("CPU fallback cannot count as GPU measurement")
        cpu,gpu=row.get("cpu_tasks"),row.get("gpu_tasks")
        if type(cpu) is not int or type(gpu) is not int or cpu<0 or gpu<0 or cpu+gpu!=count:
            raise ValueError("invalid task accounting")
        if expected_backend!="user_cpu" and gpu<=0:
            raise ValueError("no work completed on GPU")
        if expected_backend in ("user_cuda","ref8_cuda") and gpu!=count:
            raise ValueError("strict CUDA backend did not complete all tasks")
        if expected_backend=="user_cpu" and gpu!=0:
            raise ValueError("CPU row includes GPU work")

def validate_output(path,n,pairs):
    values=Path(path).read_text(encoding="ascii").splitlines()
    if len(values)!=len(pairs) or any(not v.isdigit() for v in values):
        raise ValueError("ciphertext count or encoding mismatch")
    for i,(value,(m,r)) in enumerate(zip(values,pairs)):
        c=int(value)
        # Independent Python integer arithmetic, outside native timers.
        if not 0<=c<n*n or c!=encrypt(n,m,r):
            raise ValueError(f"ciphertext {i} differs from Python oracle")

def write_csv(path,rows):
    columns=list(dict.fromkeys(k for row in rows for k in row))
    with path.open("x",newline="",encoding="utf-8-sig") as f:
        writer=csv.DictWriter(f,fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

def summarize(rows):
    groups={}
    for r in rows:
        groups.setdefault((r["requested_n_bits"],r["count"],r["backend"]),[]).append(r)
    medians={k:statistics.median(r["wall_seconds"] for r in v) for k,v in groups.items()}
    summary=[]
    for (bits,count,backend),values in groups.items():
        wall=medians[bits,count,backend]
        cpu=medians.get((bits,count,"user_cpu"))
        ref8=medians.get((bits,count,"ref8_cuda"))
        summary.append({"requested_n_bits":bits,"actual_n_bits":values[0]["n_bits"],
            "messages":count,"dataset":values[0]["dataset"],"backend":backend,
            "repeats":len(values),"median_wall_seconds":wall,
            "median_kernel_ms":statistics.median(r["kernel_ms"] for r in values),
            "ciphertexts_per_second":count/wall,
            "speedup_vs_user_cpu":cpu/wall if cpu is not None else "",
            "speedup_vs_ref8_cuda":ref8/wall if ref8 is not None else "",
            "verification":"PASS","fixture_sha256":values[0]["fixture_sha256"]})
    return summary

def run(a):
    if a.repeat<1 or a.warmup<0 or a.threads<1 or a.device<0 or a.timeout<=0:
        raise ValueError("invalid run configuration")
    if any(b not in BACKENDS for b in a.backends) or len(set(a.backends))!=len(a.backends):
        raise ValueError("invalid or duplicate backends")
    if any(n<1 for n in a.messages) or len(set(a.messages))!=len(a.messages):
        raise ValueError("invalid or duplicate message counts")
    if any(b<64 or b%16 or b>4096 for b in a.bits) or len(set(a.bits))!=len(a.bits):
        raise ValueError("invalid or duplicate modulus sizes")
    bins={}
    for b in a.backends:
        for bits in a.bits:
            if b=="ref8_cuda" and bits not in (512,1024,2048,3072,4096):
                raise ValueError("ref8_cuda sizes: 512/1024/2048/3072/4096")
            bins[b,bits]=binary(a.build,"ref8_bench_"+str(bits) if b=="ref8_cuda" else "user_bench")
    a.output.mkdir(parents=True,exist_ok=False)
    a._created_output=True
    (a.output/"inputs").mkdir()
    (a.output/"logs").mkdir()
    (a.output/"ciphertexts").mkdir()
    environment={"python":sys.version,"platform":platform.platform(),"arguments":vars(a),
                 "sources":json.loads((ROOT/"provenance.json").read_text(encoding="utf-8")) if (ROOT/"provenance.json").exists() else None}
    try:
        environment["nvidia_smi"]=subprocess.run(["nvidia-smi"],capture_output=True,text=True,timeout=15).stdout
    except (OSError,subprocess.TimeoutExpired) as exc:
        environment["nvidia_smi_error"]=str(exc)
    (a.output/"environment.json").write_text(json.dumps(environment,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    all_rows=[]
    case_index=0
    for bits in a.bits:
        # Same key and prefix operands for every batch size at this requested width.
        n,pairs,metadata=make_case(bits,max(a.messages),a.dataset,a.slot_bytes,None if a.seed is None else a.seed+bits)
        for count in a.messages:
            tag=f"n{bits}_mc{count}"
            fixture=a.output/"inputs"/(tag+".txt")
            meta=write_case(fixture,n,pairs[:count],dict(metadata,count=count))
            expected_n,expected_pairs=read_case(fixture)
            # Rotate which backend runs first as batch sizes change.
            order=a.backends[case_index%len(a.backends):]+a.backends[:case_index%len(a.backends)]
            case_index+=1
            for backend in order:
                ciphertext=a.output/"ciphertexts"/(tag+"_"+backend+".txt")
                exe=bins[backend,bits]
                cmd=[str(exe),"--input",str(fixture.resolve()),"--output",str(ciphertext.resolve()),
                     "--repeat",str(a.repeat),"--warmup",str(a.warmup),"--device",str(a.device)]
                if backend.startswith("user_"):
                    cmd += ["--backend",backend[5:],"--threads",str(a.threads),
                            "--gpu-chunk",str(a.gpu_chunk),"--gpu-max-chunk",str(a.gpu_max_chunk)]
                print(f"Running {tag} {backend}",flush=True)
                start=time.perf_counter()
                completed=subprocess.run(cmd,capture_output=True,text=True,encoding="utf-8",timeout=a.timeout)
                process_seconds=time.perf_counter()-start
                log={"command":cmd,"exit_code":completed.returncode,"process_seconds":process_seconds,
                     "stdout":completed.stdout,"stderr":completed.stderr}
                (a.output/"logs"/(tag+"_"+backend+".json")).write_text(json.dumps(log,ensure_ascii=False,indent=2),encoding="utf-8")
                if completed.returncode!=0:
                    raise RuntimeError(f"{backend} failed with code {completed.returncode}; see logs")
                records=[json.loads(line) for line in completed.stdout.splitlines() if line.startswith("{")]
                validate_records(records,backend,expected_n,count,a.repeat)
                validate_output(ciphertext,expected_n,expected_pairs)
                for row in records:
                    row.update(requested_n_bits=bits,dataset=a.dataset,slot_bytes=a.slot_bytes,
                        threads=a.threads if backend.startswith("user_") else 0,device=a.device,
                        fixture_sha256=meta["sha256"],binary_sha256=digest(exe),
                        external_python_verification="PASS",process_seconds=process_seconds)
                    all_rows.append(row)
                # Preserve valid partial rows if a later configuration fails.
                with (a.output/"validated_rows.jsonl").open("a",encoding="utf-8") as f:
                    for row in records:
                        f.write(json.dumps(row)+"\n")
    write_csv(a.output/"raw.csv",all_rows)
    summary=summarize(all_rows)
    write_csv(a.output/"summary.csv",summary)
    (a.output/"status.json").write_text(json.dumps({"status":"PASS","validated_rows":len(all_rows),
        "comparison_scope":"ordinary Paillier encryption operator; not full ZKP",
        "timing":"native warm operator wall time; RNG, setup, oracle excluded"},indent=2),encoding="utf-8")
    print(f"COMPARISON=PASS; results: {a.output.resolve()}")

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--build",type=Path,default=ROOT/"build")
    ap.add_argument("--bits",type=int,nargs="+",default=[512,1024,2048])
    ap.add_argument("--messages",type=int,nargs="+",default=[1,10,40,100,200,400])
    ap.add_argument("--backends",nargs="+",choices=BACKENDS,default=list(BACKENDS))
    ap.add_argument("--dataset",choices=["structured","uniform","edges"],default="structured")
    ap.add_argument("--slot-bytes",type=int,default=4)
    ap.add_argument("--repeat",type=int,default=5)
    ap.add_argument("--warmup",type=int,default=1)
    ap.add_argument("--threads",type=int,default=16)
    ap.add_argument("--device",type=int,default=0)
    ap.add_argument("--gpu-chunk",type=int,default=1)
    ap.add_argument("--gpu-max-chunk",type=int,default=32)
    ap.add_argument("--seed",type=int,help="synthetic reproducible test fixtures only")
    ap.add_argument("--timeout",type=float,default=3600)
    ap.add_argument("--output",type=Path,required=True)
    a=ap.parse_args()
    a._created_output=False
    try:
        run(a)
    except Exception as exc:
        if a._created_output:
            (a.output/"failure.json").write_text(json.dumps({"status":"FAILED","error":str(exc)},indent=2),encoding="utf-8")
        print(f"COMPARISON=FAILED: {exc}",file=sys.stderr)
        return 1
    return 0

if __name__=="__main__":
    raise SystemExit(main())
