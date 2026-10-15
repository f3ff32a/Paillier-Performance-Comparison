#!/usr/bin/env python3
"""只读诊断串行项目所需编译器、NTL/GMP 和动态库；不安装或改配置。"""

import argparse
import os
from pathlib import Path
import re
import shlex
import shutil
import sys

# 导入辅助代码时也不向项目目录写入 __pycache__。
sys.dont_write_bytecode = True

from build_info import (compiler_command, compiler_library,
                        dynamic_libraries, header_probe, os_information, run,
                        serial_policy)


def version_tuple(value):
    if not value:
        return ()
    match = re.search(r"CXXABI_([0-9]+(?:\.[0-9]+)+)", value)
    return tuple(map(int, match.group(1).split("."))) if match else ()


def main(argv=None):
    parser = argparse.ArgumentParser(description="只读检查 Ubuntu 串行实验依赖；不执行基准程序。")
    parser.add_argument("--compiler", default=os.environ.get("CXX", "g++"))
    parser.add_argument("--flags", default="", help="自定义头文件/库搜索选项，建议 --flags='...' ")
    parser.add_argument("--binary", default="build/serial_benchmark", help="可选：已编译程序，缺失时仍检查依赖")
    args = parser.parse_args(argv)
    try:
        compiler = compiler_command(args.compiler)
        shlex.split(args.flags)
    except ValueError as error:
        parser.error(str(error))
    errors = []
    warnings = []
    environment = os_information()
    release = environment["os_release"]
    print("串行基线依赖诊断（只读，不运行实验）")
    print("系统：{}；内核：{}；架构：{}".format(
        release.get("PRETTY_NAME", environment["system"]), environment["kernel"], environment["architecture"]))
    print("编译器：" + args.compiler)
    compiler_version = run(compiler + ["--version"])
    if compiler_version["returncode"] != 0:
        errors.append("编译器不可用：" + compiler_version["stderr"])
    else:
        print("[通过] " + compiler_version["stdout"].splitlines()[0])
    headers = header_probe(compiler, args.flags)
    if headers["ok"]:
        versions = headers["versions"]
        gmp_version = ".".join(versions.get(name) or "?" for name in
                              ("__GNU_MP_VERSION", "__GNU_MP_VERSION_MINOR", "__GNU_MP_VERSION_PATCHLEVEL"))
        print("[通过] NTL 头文件版本={}；GMP 头文件版本={}".format(versions.get("NTL_VERSION"), gmp_version))
        for path in headers["header_paths"]:
            if path.endswith(("NTL/ZZ.h", "NTL/version.h", "NTL/config.h", "gmp.h")):
                print("  头文件：" + path)
        print("  NTL 头文件启用线程支持：{}（支持线程不代表本程序使用多线程）".format(
            "是" if headers["ntl_headers_built_with_threads"] else "否"))
        if any(path.startswith("/usr/local/") for path in headers["header_paths"]):
            warnings.append("NTL/GMP 头文件来自 /usr/local；请确认它与实际链接库为同一次安装，避免混用版本。")
    else:
        errors.append("NTL/GMP 头文件检查失败：\n" + headers["error"])
    linker_libraries = [compiler_library(compiler, name, args.flags)
                        for name in ("libntl.so", "libgmp.so", "libstdc++.so.6")]
    for library in linker_libraries:
        if library["exists"]:
            print("[通过] 编译器找到 {}：{}".format(library["name"], library["resolved_path"]))
            if library.get("link_kind") == "static archive":
                print("  静态库回退候选；实际二进制的动态依赖另由 ldd 核对。")
            for package in library.get("package_versions", []):
                print("  软件包 {} {}".format(package["package"], package["version"]))
        else:
            errors.append("编译器找不到 {}；需要对应开发包及正确的库搜索路径。".format(library["name"]))
    ldconfig = shutil.which("ldconfig")
    if not ldconfig:
        ldconfig = next((path for path in ("/sbin/ldconfig", "/usr/sbin/ldconfig") if Path(path).is_file()), None)
    cached_paths = []
    if ldconfig:
        cache = run([ldconfig, "-p"])
        if cache["returncode"] == 0:
            matches = [line.strip() for line in cache["stdout"].splitlines()
                       if re.search(r"\blib(?:ntl|gmp|stdc\+\+)\.", line)]
            print("动态库缓存（ldconfig -p）：")
            for line in matches:
                print("  " + line)
                if "=>" in line:
                    cached_paths.append(line.split("=>", 1)[1].strip())
            if not matches:
                warnings.append("动态库缓存中未找到 NTL/GMP；自定义库可能需要显式 rpath 或正确加载路径。")
        else:
            warnings.append("无法读取 ldconfig 缓存：" + cache["stderr"])
    else:
        warnings.append("ldconfig 不可用，无法读取动态库缓存。")
    binary = Path(args.binary).resolve()
    inspected = []
    if binary.is_file():
        linkage = dynamic_libraries(binary)
        inspected.append(("基准程序", linkage))
        print("已编译程序：" + str(binary))
    else:
        linkage = {"libraries": []}
        print("[未构建] " + str(binary))
        print("  继续诊断依赖；构建后再次执行 doctor 以确认程序实际加载的库。")
    policy = serial_policy(args.flags, linkage)
    errors.extend(policy["violations"])
    # Even before a build, inspect the linker-selected NTL's runtime dependencies.
    for library in linker_libraries:
        if library["name"] == "libntl.so" and library["exists"]:
            inspected.append(("链接器选择的 NTL", dynamic_libraries(library["resolved_path"])))
    runtime_records = []
    for label, linkage in inspected:
        print("{}的 ldd 结果：".format(label))
        for line in (linkage.get("stdout", "") + "\n" + linkage.get("stderr", "")).strip().splitlines():
            print("  " + line)
        if not linkage["ok"]:
            errors.append(label + "动态库加载检查未通过；注意上面的 not found / CXXABI 错误。")
        runtime_records.extend(linkage.get("libraries", []))
    local_records = [library for library in linker_libraries + runtime_records
                     if library.get("is_usr_local")]
    if local_records or any(path.startswith("/usr/local/") for path in cached_paths):
        warnings.append("存在 /usr/local 下的 NTL/GMP/libstdc++ 候选或已加载库。该目录中的旧库可能优先于 apt 库；请以程序 ldd 的实际路径为准。")
    for key in ("LD_LIBRARY_PATH", "LIBRARY_PATH", "CPATH", "CPLUS_INCLUDE_PATH"):
        if os.environ.get(key):
            print("搜索路径 {}={}".format(key, os.environ[key]))
            warnings.append(key + " 已设置，可能影响编译或加载路径；请检查是否指向旧安装。")
    # Compare selected/loaded NTL requirements with its own runtime libstdc++.
    for library in linker_libraries + runtime_records:
        if library.get("name", "").startswith("libntl") and library.get("exists") and library.get("link_kind") != "static archive":
            required = library.get("abi_version_tags", {}).get("max_cxxabi_tag")
            ntl_linkage = dynamic_libraries(library["resolved_path"])
            stdlibs = [item for item in ntl_linkage["libraries"] if item["name"].startswith("libstdc++")]
            for stdlib in stdlibs:
                available = stdlib.get("abi_version_tags", {}).get("max_cxxabi_tag")
                if required and available and version_tuple(required) > version_tuple(available):
                    errors.append("NTL 需要 {}，但实际加载的 {} 最高仅 {}；这是 CXXABI 不匹配。".format(
                        required, stdlib["resolved_path"], available))
    print("线程与串行边界：NTL 目标线程数为 1；doctor 不运行程序，ldd 成功不代表运行时线程数已确认。")
    print("  -pthread 仅用于依赖链接；本项目不启用 OpenMP、CUDA 或 coverage。")
    for warning in dict.fromkeys(warnings):
        print("[注意] " + warning)
    for error in dict.fromkeys(errors):
        print("[未通过] " + error)
    if errors or warnings:
        print("建议：")
        print("  1. 缺少标准依赖时，执行 bash scripts/install_ubuntu.sh，再 make clean && make。")
        print("  2. 若 /usr/local 与 apt 版本混用，先核对上面头文件、库路径及软件包归属，再选择同一套依赖重新构建。")
        print("  3. CXXABI 错误应使 NTL 与运行时 libstdc++ 来自兼容工具链；不要随意替换 libstdc++.so 或删除系统库。")
        print("本诊断没有安装软件、删除文件或修改系统配置。")
    if errors:
        print("结果：依赖或链接检查未通过。")
        return 1
    print("结果：当前依赖检查通过{}。".format("；程序实际链接仍须在构建后确认" if not binary.is_file() else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
