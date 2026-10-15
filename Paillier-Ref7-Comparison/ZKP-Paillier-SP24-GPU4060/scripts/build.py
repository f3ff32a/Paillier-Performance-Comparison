#!/usr/bin/env python3
"""Build CPU or RTX 4060 CUDA targets with one consistent host compiler."""
import argparse
import datetime
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def executable(value):
    found = shutil.which(str(value))
    if not found:
        raise RuntimeError(f"Executable not found: {value}")
    return str(Path(found).absolute())


def run(command):
    print("+ " + shlex.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), check=True, cwd=ROOT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["cpu", "cuda"], default="cuda")
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--cxx", default="/usr/bin/g++" if Path("/usr/bin/g++").exists() else "g++")
    parser.add_argument("--nvcc", help="CUDA compiler; defaults to CUDACXX, CUDA 13.4, then PATH")
    parser.add_argument("--cmake", default=os.environ.get("CMAKE", "cmake"))
    parser.add_argument("--arch", default="89-real", help="native RTX 4060 SASS by default")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--fresh", action="store_true", help="rename an existing build directory before configuring")
    parser.add_argument("cmake_args", nargs=argparse.REMAINDER, help="extra CMake arguments after --")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    extra = args.cmake_args
    if extra and extra[0] == "--":
        extra = extra[1:]
    # Core options have explicit CLI switches. Avoid contradictory cache entries.
    if any(s.startswith(("-DCMAKE_CXX_COMPILER", "-DCMAKE_CUDA_HOST_COMPILER",
                         "-DCMAKE_CUDA_COMPILER", "-DCMAKE_CUDA_ARCHITECTURES",
                         "-DZKP_ENABLE_CUDA")) for s in extra):
        parser.error("use --cxx, --nvcc, --arch, --backend for compiler/backend selection")
    if any(os.environ.get(k) for k in ("NVCC_PREPEND_FLAGS", "NVCC_APPEND_FLAGS")):
        raise RuntimeError("NVCC_PREPEND_FLAGS / NVCC_APPEND_FLAGS are set. Review and unset them "
                           "so external flags cannot override this build's host compiler.")
    cmake, cxx = executable(args.cmake), executable(args.cxx)
    directory = args.build_dir or Path("build-rtx4060" if args.backend == "cuda" else "build-cpu")
    if not directory.is_absolute():
        directory = ROOT / directory
    directory = directory.resolve()
    if directory == ROOT or directory in ROOT.parents:
        raise RuntimeError("the build directory must be separate from the project source")
    nvcc = None
    if args.backend == "cuda":
        candidates = [args.nvcc] if args.nvcc else [os.environ.get("CUDACXX"),
                      "/usr/local/cuda-13.4/bin/nvcc", "nvcc", "/usr/local/cuda/bin/nvcc"]
        nvcc = next((executable(v) for v in candidates if v and shutil.which(v)), None)
        if not nvcc:
            raise RuntimeError("nvcc not found. Install CUDA Toolkit 13.4 for Ubuntu 26.04, "
                               "or pass --nvcc /path/to/nvcc. See docs/UBUNTU_26_RTX4060.md.")
    desired = {"CMAKE_CXX_COMPILER": cxx}
    if nvcc:
        desired.update(CMAKE_CUDA_COMPILER=nvcc, CMAKE_CUDA_HOST_COMPILER=cxx)
    if args.fresh and directory.exists():
        if not (directory / "CMakeCache.txt").is_file():
            raise RuntimeError("--fresh only renames directories containing CMakeCache.txt")
        backup = directory.with_name(directory.name + ".previous-" +
                                    datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        directory.rename(backup)
        print(f"Previous build preserved: {backup}", flush=True)
    cache = directory / "CMakeCache.txt"
    if cache.exists():
        values = {}
        for line in cache.read_text().splitlines():
            if not line.startswith(("//", "#")) and "=" in line and ":" in line:
                key, value = line.split("=", 1)
                values[key.split(":", 1)[0]] = value
        old_source = values.get("CMAKE_HOME_DIRECTORY")
        if old_source and Path(old_source).resolve() != ROOT:
            raise RuntimeError("Build directory belongs to another project; use --fresh or --build-dir.")
        for key, value in desired.items():
            if key in values and Path(values[key]).resolve() != Path(value).resolve():
                raise RuntimeError(f"Cached {key} differs. Use --fresh or a new --build-dir.")
    run([cxx, "--version"])
    command = [cmake, "-S", ROOT, "-B", directory, "-DCMAKE_BUILD_TYPE=Release",
               "-DZKP_BUILD_TESTS=ON", "-DZKP_ENABLE_OPENMP=ON",
               "-DZKP_ENABLE_CUDA=" + ("ON" if nvcc else "OFF")]
    command.extend("-D" + key + "=" + value for key, value in desired.items())
    if nvcc:
        run([nvcc, "--version"])
        command.append("-DCMAKE_CUDA_ARCHITECTURES=" + args.arch)
    command.extend(extra)
    run(command)
    run([cmake, "--build", directory, "--parallel", str(args.jobs)])
    print(f"BUILD=PASS {directory}\nBuild metadata: {directory / 'build-info.txt'}")
    if nvcc:
        print("Next: python3 scripts/validate_gpu.py --build-dir " + shlex.quote(str(directory)))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"BUILD=FAIL {error}", file=sys.stderr)
        sys.exit(1)
