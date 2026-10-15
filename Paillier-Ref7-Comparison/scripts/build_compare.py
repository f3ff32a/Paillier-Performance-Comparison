#!/usr/bin/env python3
"""Configure/build the extracted user backend without the full NTL protocol."""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["cpu", "cuda"], default="cuda")
    parser.add_argument("--cxx", help="C++ compiler, e.g. /usr/bin/g++")
    parser.add_argument("--nvcc", help="CUDA compiler path")
    parser.add_argument("--arch", default="89-real", help="RTX 4060 native SASS by default")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--test", action="store_true", help="test CPU and, for CUDA builds, real GPU execution")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("jobs must be positive")
    cmake = shutil.which("cmake")
    if cmake is None:
        parser.error("CMake is missing; install the build dependencies described in the usage guide")
    build = (args.build_dir or ROOT / ("build-zkp-compare-" + args.backend)).resolve()
    config = [cmake, "-S", str(ROOT / "zkp_adapter"), "-B", str(build),
              "-DCMAKE_BUILD_TYPE=Release", "-DZKP_COMPARE_CUDA=" + ("ON" if args.backend == "cuda" else "OFF")]
    if args.cxx:
        config.append("-DCMAKE_CXX_COMPILER=" + args.cxx)
    if args.backend == "cuda":
        config.append("-DCMAKE_CUDA_ARCHITECTURES=" + args.arch)
        if args.cxx:
            config.append("-DCMAKE_CUDA_HOST_COMPILER=" + args.cxx)
        if args.nvcc:
            config.append("-DCMAKE_CUDA_COMPILER=" + args.nvcc)
    subprocess.run(config, check=True)
    subprocess.run([cmake, "--build", str(build), "--parallel", str(args.jobs)], check=True)
    suffix = ".exe" if sys.platform == "win32" else ""
    binary = build / ("paillier_compare" + suffix)
    if not binary.exists():
        # Handle a multi-configuration generator without changing the Linux defaults.
        binary = build / "Release" / ("paillier_compare" + suffix)
    if not binary.is_file():
        raise RuntimeError(f"built executable not found: {binary}")
    if args.test:
        command = [sys.executable, str(ROOT / "zkp_adapter" / "test_adapter.py"), "--binary", str(binary)]
        if args.backend == "cuda":
            command.append("--gpu")
        subprocess.run(command, check=True)
    print(f"BUILD=PASS binary={binary}")


if __name__ == "__main__":
    main()
