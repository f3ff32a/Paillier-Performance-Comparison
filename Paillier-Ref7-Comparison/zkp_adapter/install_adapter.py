#!/usr/bin/env python3
"""Install an isolated ordinary-encryption benchmark into an existing ZKP project."""
import argparse
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="ZKP-Paillier-SP24-GPU4060 project root")
    args = parser.parse_args()
    root = args.project.resolve(strict=True)
    here = Path(__file__).resolve().parent
    cmake = root / "CMakeLists.txt"
    required = [cmake, root / "src/cpp/app/acceleration/CudaBackend.hpp",
                root / "src/cpp/app/acceleration/Backend.hpp", root / "src/cpp/vendor/json/json.hpp"]
    if not all(path.is_file() for path in required):
        parser.error("project does not contain the expected ZKP CMake and acceleration interfaces")
    marker = '# Paillier-Ref7-Comparison adapter (standalone benchmark only)'
    include = 'include(cmake/PaillierComparison.cmake)'
    original = cmake.read_text(encoding="utf-8")
    source = root / "tools/paillier_compare.cpp"
    fragment = root / "cmake/PaillierComparison.cmake"
    for destination, expected in [(source, here / "paillier_compare.cpp"),
                                  (fragment, here / "CMakeLists.fragment.cmake")]:
        if destination.exists() and destination.read_bytes() != expected.read_bytes():
            parser.error(f"refusing to replace a different existing adapter file: {destination}")
    if include in original and marker not in original:
        parser.error("project already contains a different PaillierComparison include")
    for destination, expected in [(source, here / "paillier_compare.cpp"),
                                  (fragment, here / "CMakeLists.fragment.cmake")]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copyfile(expected, destination)
    if marker not in original:
        backup = cmake.with_name("CMakeLists.txt.before-paillier-comparison")
        if backup.exists():
            parser.error(f"backup already exists; inspect it before installing: {backup}")
        shutil.copyfile(cmake, backup)
        cmake.write_text(original.rstrip() + "\n\n" + marker + "\n" + include + "\n", encoding="utf-8")
    print(f"Installed {source}")
    print("Build: cmake --build build-rtx4060 --target paillier_compare --parallel 4")
    print("Protocol sources were not edited; root CMake include has a backup.")


if __name__ == "__main__":
    main()
