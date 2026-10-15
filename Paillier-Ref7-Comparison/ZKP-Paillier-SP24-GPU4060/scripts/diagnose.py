#!/usr/bin/env python3
"""Read-only compiler, library, NVIDIA and CUDA diagnostics; Python stdlib only."""
import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def capture(command, timeout=60):
    try:
        result = subprocess.run(list(map(str, command)), text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=timeout)
        return {"command": list(map(str, command)), "returncode": result.returncode, "output": result.stdout}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"command": list(map(str, command)), "returncode": -1, "output": str(error)}


def collect(build_dir, cxx="/usr/bin/g++", nvcc="nvcc", device=0):
    report = {"project": "ZKP-Paillier-SP24-GPU2",
              "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "platform": platform.platform(), "device": device,
              "os_release": Path("/etc/os-release").read_text() if Path("/etc/os-release").exists() else "",
              "environment": {k: os.environ[k] for k in ["CC", "CXX", "CUDACXX", "CUDAHOSTCXX",
                  "NVCC_CCBIN", "NVCC_PREPEND_FLAGS", "NVCC_APPEND_FLAGS", "LIBRARY_PATH",
                  "LD_LIBRARY_PATH", "CONDA_PREFIX", "CUDA_VISIBLE_DEVICES", "CUDA_FORCE_PTX_JIT"]
                              if k in os.environ}, "checks": {}}
    info = build_dir / "build-info.txt"
    if info.exists():
        report["build_info"] = info.read_text()
        values = dict(line.split("=", 1) for line in info.read_text().splitlines() if "=" in line)
        cxx = values.get("cxx") or cxx
        nvcc = values.get("cuda_compiler") or nvcc
    else:
        values = {}
    checks = report["checks"]
    commands = {"cxx": [cxx, "--version"], "libstdcxx_path": [cxx, "-print-file-name=libstdc++.so"],
                "nvcc": [nvcc, "--version"], "nvidia_smi": ["nvidia-smi"],
                "packages": ["dpkg-query", "-W", "g++", "libstdc++6", "libntl-dev", "libgmp-dev"],
                "virtualization": ["systemd-detect-virt"]}
    for name, command in commands.items():
        checks[name] = capture(command)
    if shutil.which("lspci"):
        pci = capture(["lspci", "-nn"])
        pci["output"] = "\n".join(s for s in pci["output"].splitlines()
                                    if any(k in s.lower() for k in ("nvidia", "vga", "3d controller")))
        checks["gpu_pci"] = pci
    if shutil.which(cxx):
        with tempfile.TemporaryDirectory(prefix="zkp-abi-") as temp:
            source, binary = Path(temp) / "probe.cpp", Path(temp) / "probe"
            source.write_text('#include <NTL/ZZ.h>\n#include <sstream>\nint main(){'
                              'std::ostringstream s; s << NTL::PowerMod(NTL::ZZ(3),NTL::ZZ(5),NTL::ZZ(17));'
                              'return s.str()=="5" ? 0 : 1;}\n')
            flags = ["-I" + values[key] for key in ("ntl_include", "gmp_include") if values.get(key)]
            checks["ntl_link"] = capture([cxx, "-std=c++14", str(source), *flags,
                values.get("ntl") or "-lntl", values.get("gmp") or "-lgmp", "-pthread", "-o", binary])
            if checks["ntl_link"]["returncode"] == 0:
                checks["ntl_runtime"] = capture([binary])
                checks["ntl_runtime_libraries"] = capture(["ldd", binary])
    binary = build_dir / "zkp_bench"
    if binary.exists():
        checks["program_libraries"] = capture(["ldd", binary])
    probe = build_dir / "zkp_device_info"
    if probe.exists():
        checks["cuda_kernel"] = capture([probe, "--device", device])
    else:
        checks["cuda_kernel"] = {"returncode": -1, "output": "Build CUDA targets first; zkp_device_info is missing."}
    report["cuda_probe_pass"] = (checks["cuda_kernel"]["returncode"] == 0 and
                                  "CUDA_PROBE=PASS" in checks["cuda_kernel"]["output"])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build-rtx4060")
    parser.add_argument("--cxx", default="/usr/bin/g++")
    parser.add_argument("--nvcc", default="nvcc")
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--output", type=Path, help="new JSON file to save the report")
    parser.add_argument("--require-gpu", action="store_true", help="fail unless a real CUDA kernel passed")
    args = parser.parse_args()
    if args.device < 0:
        parser.error("--device must be nonnegative")
    report = collect(args.build_dir.resolve(), args.cxx, args.nvcc, args.device)
    text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        with args.output.open("x") as output:
            output.write(text)
        print(f"Report: {args.output}")
    else:
        print(text)
    if args.require_gpu and not report["cuda_probe_pass"]:
        raise RuntimeError("CUDA kernel probe did not pass. See docs/UBUNTU_26_RTX4060.md.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
