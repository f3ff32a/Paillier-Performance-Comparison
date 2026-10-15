#!/usr/bin/env python3
"""记录串行程序的构建证据；不执行基准程序，不修改系统环境。"""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STAGE_HEADERS = "#include <NTL/ZZ.h>\n#include <NTL/version.h>\n#include <gmp.h>\n"


def run(command, input_text=None, timeout=15):
    """所有命令均使用参数数组；不通过 shell 展开用户输入。"""
    try:
        result = subprocess.run(command, input=input_text, text=True,
                                capture_output=True, timeout=timeout,
                                env={**os.environ, "LC_ALL": "C"})
        return {"command": command, "returncode": result.returncode,
                "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"command": command, "returncode": None,
                "stdout": "", "stderr": str(error)}


def compiler_command(value):
    command = shlex.split(value)
    if not command:
        raise ValueError("编译器命令不能为空")
    return command


def include_options(flags):
    """只向预处理器传递头文件相关选项，避免引入链接/插桩行为。"""
    values = shlex.split(flags)
    selected = []
    separate = {"-I", "-D", "-U", "-isystem", "-iquote", "-idirafter",
                "-include", "-imacros", "--sysroot", "-isysroot"}
    index = 0
    while index < len(values):
        value = values[index]
        if value in separate:
            selected.append(value)
            if index + 1 < len(values):
                index += 1
                selected.append(values[index])
        elif value.startswith(("-I", "-D", "-U", "--sysroot=", "-std=")):
            selected.append(value)
        elif value in ("-nostdinc", "-nostdinc++"):
            selected.append(value)
        index += 1
    return selected


def header_probe(compiler, flags=""):
    options = include_options(flags)
    result = run(compiler + options + ["-E", "-dM", "-x", "c++", "-"], STAGE_HEADERS)
    macros = {}
    if result["returncode"] == 0:
        for line in result["stdout"].splitlines():
            match = re.match(r"#define\s+(\w+)\s*(.*)", line)
            if match:
                macros[match.group(1)] = match.group(2)
    dependencies = run(compiler + options + ["-M", "-x", "c++", "-"], STAGE_HEADERS)
    paths = sorted(set(re.findall(r"(?:/|\./)[^\s\\]+", dependencies["stdout"].replace("\\\n", " "))))
    versions = {name: macros.get(name) for name in (
        "NTL_VERSION", "NTL_MAJOR_VERSION", "NTL_MINOR_VERSION", "NTL_REVISION",
        "__GNU_MP_VERSION", "__GNU_MP_VERSION_MINOR", "__GNU_MP_VERSION_PATCHLEVEL")}
    return {"ok": result["returncode"] == 0, "returncode": result["returncode"],
            "error": result["stderr"], "command": result["command"],
            "versions": versions, "ntl_headers_built_with_threads": "NTL_THREADS" in macros,
            "header_paths": [p for p in paths if "/NTL/" in p or p.endswith("gmp.h")]}


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def os_information():
    release = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                try:
                    parsed = shlex.split(value)
                    release[key] = parsed[0] if len(parsed) == 1 else value
                except ValueError:
                    release[key] = value
    except OSError:
        pass
    cpu_models = []
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            key, _, value = line.partition(":")
            if key.strip() in ("model name", "Hardware") and value.strip() not in cpu_models:
                cpu_models.append(value.strip())
    except OSError:
        pass
    ram_bytes = None
    try:
        memory = Path("/proc/meminfo").read_text()
        found = re.search(r"^MemTotal:\s+(\d+)\s+kB", memory, re.M)
        if found:
            ram_bytes = int(found.group(1)) * 1024
    except OSError:
        pass
    affinity_count = None
    if hasattr(os, "sched_getaffinity"):
        try:
            affinity_count = len(os.sched_getaffinity(0))
        except OSError:
            pass
    return {"os_release": release, "kernel": platform.release(),
            "system": platform.system(), "architecture": platform.machine(),
            "cpu_models": cpu_models, "logical_cpus_os": os.cpu_count(),
            "logical_cpus_process_affinity": affinity_count,
            "ram_total_bytes": ram_bytes,
            "ram_total_gib": ram_bytes / (1024 ** 3) if ram_bytes is not None else None,
            "memory_note": "物理内存来自 /proc/meminfo；未测量运行时峰值或容器内存上限。"}


def package_information(path):
    if not shutil.which("dpkg-query"):
        return []
    results = []
    paths = list(dict.fromkeys([str(path), str(Path(path).resolve())]))
    for candidate in paths:
        owner = run(["dpkg-query", "-S", candidate])
        if owner["returncode"] != 0:
            continue
        for line in owner["stdout"].splitlines():
            if ": " not in line:
                continue
            package = line.split(": ", 1)[0]
            info = run(["dpkg-query", "-W", "-f=${binary:Package}\t${Version}", package])
            if info["returncode"] == 0:
                entry = {"package": package, "version": info["stdout"].split("\t", 1)[-1]}
                if entry not in results:
                    results.append(entry)
    return results


def abi_versions(path):
    """记录库的 CXXABI 版本标签；供诊断参考，不替代实际加载检查。"""
    if not shutil.which("readelf"):
        return {"available": False, "note": "readelf 不可用"}
    result = run(["readelf", "--version-info", str(path)])
    found = sorted(set(re.findall(r"CXXABI_([0-9]+(?:\.[0-9]+)+)", result["stdout"])),
                   key=lambda item: tuple(map(int, item.split("."))))
    return {"available": result["returncode"] == 0,
            "cxxabi_tags": ["CXXABI_" + item for item in found],
            "max_cxxabi_tag": "CXXABI_" + found[-1] if found else None,
            "error": result["stderr"]}


def library_record(name, path):
    resolved = Path(path).resolve()
    record = {"name": name, "path": path, "resolved_path": str(resolved),
              "is_usr_local": str(resolved).startswith("/usr/local/"),
              "exists": resolved.is_file()}
    if resolved.is_file():
        record.update({"sha256": file_hash(resolved),
                       "package_versions": package_information(path),
                       "abi_version_tags": abi_versions(resolved)})
        record["library_filename_version"] = resolved.name
        record["version_note"] = ("软件包版本来自 dpkg-query；库文件名中的 SONAME 版本不等同于项目发行版本。"
                                  if record["package_versions"] else
                                  "未找到 dpkg 软件包归属，实际发行版本未知；保留文件名和 SHA256，不能用头文件版本代替运行库版本。")
    return record


def dynamic_libraries(path):
    result = run(["ldd", str(path)])
    combined = result["stdout"] + "\n" + result["stderr"]
    libraries = []
    missing = []
    for line in combined.splitlines():
        match = re.match(r"\s*(\S+)\s+=>\s+(\S+)", line)
        if match:
            name, target = match.groups()
            if target == "not":
                missing.append(name)
            elif target.startswith("/"):
                libraries.append(library_record(name, target))
        else:
            match = re.match(r"\s*(/\S+)\s+\(", line)
            if match:
                libraries.append(library_record(Path(match.group(1)).name, match.group(1)))
    loader_errors = [line.strip() for line in combined.splitlines()
                     if "not found" in line or "version `" in line or "undefined symbol" in line]
    return {"returncode": result["returncode"], "stdout": result["stdout"],
            "stderr": result["stderr"], "libraries": libraries,
            "missing_libraries": sorted(set(missing)), "loader_errors": loader_errors,
            "ok": result["returncode"] == 0 and not missing and not loader_errors}


def compiler_library(compiler, name, flags=""):
    # GCC -print-file-name does not reliably honour -L. Check explicit -L first,
    # in its linker search order, then ask GCC for its default library path.
    options = []
    library_dirs = []
    values = shlex.split(flags)
    index = 0
    while index < len(values):
        value = values[index]
        if value in ("-L", "--sysroot"):
            options.append(value)
            if index + 1 < len(values):
                index += 1
                options.append(values[index])
                if value == "-L":
                    library_dirs.append(values[index])
        elif value.startswith(("-L", "--sysroot=")):
            options.append(value)
            if value.startswith("-L"):
                library_dirs.append(value[2:])
        index += 1
    for directory in library_dirs:
        names = [name, name[:-3] + ".a"] if name.endswith(".so") else [name]
        for candidate_name in names:
            candidate = Path(directory) / candidate_name
            if candidate.is_file():
                record = library_record(candidate_name, str(candidate))
                record["requested_library"] = name
                record["link_kind"] = "static archive" if candidate_name.endswith(".a") else "shared library"
                record["search_evidence"] = "explicit -L directory; actual runtime path is recorded separately by ldd"
                return record
    result = run(compiler + options + ["-print-file-name=" + name])
    value = result["stdout"].strip()
    if result["returncode"] == 0 and value and value != name and Path(value).is_file():
        record = library_record(name, value)
        record["link_kind"] = "shared library"
        record["search_evidence"] = "compiler -print-file-name"
        return record
    if name.endswith(".so"):
        archive_name = name[:-3] + ".a"
        archive = run(compiler + options + ["-print-file-name=" + archive_name])
        archive_path = archive["stdout"].strip()
        if archive["returncode"] == 0 and archive_path != archive_name and Path(archive_path).is_file():
            record = library_record(archive_name, archive_path)
            record.update({"requested_library": name, "link_kind": "static archive",
                           "search_evidence": "compiler -print-file-name; static fallback"})
            return record
    return {"name": name, "exists": False, "path": value or None,
            "error": result["stderr"]}


def source_manifest():
    sources = []
    roots = [PROJECT_ROOT / "src/cpp/app", PROJECT_ROOT / "src/cpp/vendor",
             PROJECT_ROOT / "tools", PROJECT_ROOT / "scripts"]
    for root in roots:
        if root.exists():
            sources.extend(path for path in root.rglob("*")
                           if path.is_file() and path.suffix in (".cpp", ".hpp", ".h", ".cc", ".py", ".sh"))
    if (PROJECT_ROOT / "Makefile").is_file():
        sources.append(PROJECT_ROOT / "Makefile")
    records = [{"path": str(path.relative_to(PROJECT_ROOT)), "sha256": file_hash(path)}
               for path in sorted(set(sources))]
    digest = hashlib.sha256()
    for record in records:
        digest.update((record["path"] + "\0" + record["sha256"] + "\n").encode("utf-8"))
    return {"sha256": digest.hexdigest(), "algorithm": "SHA256(relative_path + NUL + file_sha256 + LF), sorted paths",
            "files": records,
            "note": "构建信息生成时的源码和构建脚本快照；不包含结果文件、文档或旧 Makefile。"}


def serial_policy(flags, linkage):
    tokens = shlex.split(flags)
    names = [record["name"] for record in linkage.get("libraries", [])]
    openmp_flags = [token for token in tokens if "openmp" in token.lower() or token in ("-lomp", "-lgomp")]
    cuda_flags = [token for token in tokens if token.lower().startswith(("-lcuda", "-lcudart")) or "nvcc" in token.lower()]
    coverage_flags = [token for token in tokens if token in ("--coverage", "-fprofile-arcs", "-ftest-coverage", "-lgcov", "-fcoverage-mapping")
                      or token.startswith("-fprofile-instr-generate")]
    openmp_libraries = [name for name in names if re.search(r"^lib(?:gomp|omp|iomp)", name)]
    cuda_libraries = [name for name in names if re.search(r"^lib(?:cuda|cudart|cublas|nvrtc)", name)]
    violations = []
    if openmp_flags or openmp_libraries:
        violations.append("发现 OpenMP 编译选项或动态库")
    if cuda_flags or cuda_libraries:
        violations.append("发现 CUDA 编译选项或动态库")
    if coverage_flags:
        violations.append("发现 coverage 插桩编译选项")
    return {"expected_openmp": False, "expected_cuda": False, "expected_coverage": False,
            "openmp_flags_found": openmp_flags, "openmp_libraries_found": openmp_libraries,
            "cuda_flags_found": cuda_flags, "cuda_libraries_found": cuda_libraries,
            "coverage_flags_found": coverage_flags, "violations": violations,
            "ntl_threads_expected": 1, "ntl_runtime_threads_confirmed": False,
            "ntl_runtime_threads_note": "本脚本不运行基准程序；ldd 成功不能证明 NTL 已在运行时设为 1 线程。请核对基准启动日志。",
            "evidence_note": "选项与动态链接证据覆盖当前构建；无法仅凭 ldd 排除人为加入的静态库。-pthread 不等同于 OpenMP。"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="记录原作者串行基线的构建和依赖信息，不运行实验。")
    parser.add_argument("--compiler", default="g++", help="编译器命令，与 Makefile 的 CXX 一致")
    parser.add_argument("--flags", default="", help="完整编译/链接选项；手动使用时建议 --flags='...' ")
    parser.add_argument("--binary", default="build/serial_benchmark", help="已编译程序路径")
    parser.add_argument("--out", default="build/build-info.json", help="JSON 输出路径")
    args = parser.parse_args(argv)
    try:
        compiler = compiler_command(args.compiler)
        shlex.split(args.flags)
    except ValueError as error:
        parser.error(str(error))
    binary = Path(args.binary).resolve()
    exists = binary.is_file()
    version = run(compiler + ["--version"])
    headers = header_probe(compiler, args.flags)
    linkage = dynamic_libraries(binary) if exists else {
        "ok": False, "libraries": [], "error": "程序不存在；尚未核对运行时动态库路径"}
    policy = serial_policy(args.flags, linkage)
    errors = []
    if version["returncode"] != 0:
        errors.append("编译器不可用：" + version["stderr"])
    if not headers["ok"]:
        errors.append("NTL/GMP 头文件检查失败：" + headers["error"])
    if not exists:
        errors.append("未找到程序：" + str(binary))
    elif not linkage["ok"]:
        errors.append("动态库解析失败；查看 dynamic_linkage 中的缺失库或 ABI 错误")
    errors.extend(policy["violations"])
    info = {
        "schema_version": 1,
        "project": "ZKP-Paillier-SP24-Serial-Ubuntu26",
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "project_root": str(PROJECT_ROOT), "working_directory": str(Path.cwd()),
        "environment": os_information(),
        "compiler": {"requested": args.compiler, "command": compiler,
                     "executable_resolved": shutil.which(compiler[0]), "version_probe": version},
        "flags": args.flags, "headers": headers,
        "linker_library_search": [compiler_library(compiler, name, args.flags)
                                  for name in ("libntl.so", "libgmp.so", "libstdc++.so.6")],
        "binary": {"path": str(binary), "exists": exists,
                   "sha256": file_hash(binary) if exists else None},
        "source": source_manifest(), "dynamic_linkage": linkage,
        "serial_policy": policy,
        "environment_search_paths": {key: os.environ.get(key) for key in
                                     ("LD_LIBRARY_PATH", "LIBRARY_PATH", "CPATH", "CPLUS_INCLUDE_PATH")},
        "checks": {"ok": not errors, "errors": errors}}
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("构建信息已写入：" + str(output))
    if errors:
        for error in errors:
            print("[未通过] " + error, file=sys.stderr)
        print("请先执行 python3 scripts/doctor.py；本脚本未修改或安装系统依赖。", file=sys.stderr)
        return 1
    print("编译器、头文件和动态库检查通过；NTL 的实际运行线程数仍须核对启动日志。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
