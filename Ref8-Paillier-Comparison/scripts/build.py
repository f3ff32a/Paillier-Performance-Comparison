"""Build the standalone operators without installing or changing system tools."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backend",choices=["cuda","cpu"],default="cuda")
    ap.add_argument("--bits",type=int,nargs="+",choices=[512,1024,2048,3072,4096],default=[512,1024,2048])
    ap.add_argument("--build-dir",type=Path)
    ap.add_argument("--cxx",default=os.environ.get("CXX","/usr/bin/g++" if os.name!="nt" else None))
    ap.add_argument("--nvcc")
    ap.add_argument("--arch",default="89-real")
    ap.add_argument("--jobs",type=int,default=4)
    a=ap.parse_args()
    if a.jobs<1:
        ap.error("jobs must be positive")
    build=(a.build_dir or ROOT/("build" if a.backend=="cuda" else "build-cpu")).resolve()
    build.mkdir(parents=True,exist_ok=True)
    command=["cmake","-S",str(ROOT),"-B",str(build),"-DCMAKE_BUILD_TYPE=Release",
        "-DCOMPARE_ENABLE_CUDA="+("ON" if a.backend=="cuda" else "OFF"),
        "-DCOMPARE_BITS="+";".join(map(str,a.bits))]
    if a.cxx:
        command += ["-DCMAKE_CXX_COMPILER="+a.cxx]
        if a.backend=="cuda":
            command += ["-DCMAKE_CUDA_HOST_COMPILER="+a.cxx]
    if a.backend=="cuda":
        command += ["-DCMAKE_CUDA_ARCHITECTURES="+a.arch]
        if a.nvcc:
            command += ["-DCMAKE_CUDA_COMPILER="+a.nvcc]
    commands=[command,["cmake","--build",str(build),"--parallel",str(a.jobs)]]
    (build/"comparison-build-command.json").write_text(json.dumps(commands,indent=2),encoding="utf-8")
    try:
        with (build/"comparison-build.log").open("w",encoding="utf-8") as log:
            for cmd in commands:
                print("Executing:"," ".join(cmd),flush=True)
                result=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT)
                if result.returncode:
                    print(f"BUILD=FAILED; see {build/'comparison-build.log'}",file=sys.stderr)
                    return result.returncode
        print(f"BUILD=PASS; binaries: {build}")
    except OSError as exc:
        print(f"BUILD=FAILED: {exc}",file=sys.stderr)
        return 1
    return 0

if __name__=="__main__":
    raise SystemExit(main())
