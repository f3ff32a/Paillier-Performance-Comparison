#!/usr/bin/env bash
# 用户主动运行时，仅安装 Ubuntu 仓库中的串行构建依赖。
set -euo pipefail

if [[ ! -r /etc/os-release ]]; then
    printf '%s\n' '无法读取 /etc/os-release；请在 Ubuntu 上执行此脚本。' >&2
    exit 1
fi
# 系统提供的标准发行版标识文件；不读取项目中的外部 shell 配置。
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]]; then
    printf '%s\n' '此安装脚本仅适用于 Ubuntu；其他发行版请手动安装 C++、Python、NTL 和 GMP 开发包。' >&2
    exit 1
fi
if ! command -v apt-get >/dev/null || ! command -v apt-cache >/dev/null; then
    printf '%s\n' '找不到 apt-get 或 apt-cache；请检查 Ubuntu 软件包管理器。' >&2
    exit 1
fi

dependencies=(build-essential python3 libntl-dev libgmp-dev)
printf '系统：%s\n' "${PRETTY_NAME:-Ubuntu}"
printf '%s\n' '将安装串行实验所需依赖：build-essential python3 libntl-dev libgmp-dev。'
printf '%s\n' '不安装 CUDA、NVIDIA 驱动、GoogleTest 或 CMake；不自动修改软件源。'
privilege=()
if (( EUID != 0 )); then
    if ! command -v sudo >/dev/null; then
        printf '%s\n' '当前用户不是 root，且未安装 sudo；请由管理员运行此脚本。' >&2
        exit 1
    fi
    privilege=(sudo)
fi

if ! "${privilege[@]}" apt-get update; then
    printf '%s\n' 'apt-get update 失败；请先修复网络或已有的软件源错误，再重新执行。脚本未修改软件源。' >&2
    exit 1
fi
missing=()
for package in "${dependencies[@]}"; do
    candidate=$(LC_ALL=C apt-cache policy "$package" | awk '$1 == "Candidate:" { print $2; exit }')
    if [[ -z "$candidate" || "$candidate" == '(none)' ]]; then
        missing+=("$package")
    fi
done
if (( ${#missing[@]} > 0 )); then
    printf '软件源中没有可安装版本：%s\n' "${missing[*]}" >&2
    if [[ " ${missing[*]} " == *' libntl-dev '* ]]; then
        printf '%s\n' 'libntl-dev 通常需要 Ubuntu universe 软件源。请在“软件和更新”中启用 universe，或由管理员执行 sudo add-apt-repository universe，然后 sudo apt-get update。' >&2
    fi
    printf '%s\n' '请确认软件源对应当前 Ubuntu 版本和架构；本脚本不会自动添加或改写仓库。' >&2
    exit 1
fi
"${privilege[@]}" apt-get install --no-install-recommends "${dependencies[@]}"
printf '%s\n' '依赖安装完成。回到项目根目录执行：python3 scripts/doctor.py，然后 make。'
