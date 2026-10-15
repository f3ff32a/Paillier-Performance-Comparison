# Ubuntu 26.04 + RTX 4060 Laptop

本文件面向笔记本真机上的 Ubuntu 26.04 x86_64。项目默认编译 RTX 4060 / RTX 4060 Laptop 的 `sm_89` 原生机器码。当前交付环境没有 NVIDIA GPU；下列配置是适配目标，实机是否通过以 `validate_gpu.py` 的报告为准。

## 工具链选择

| 组件 | 本项目建议配置 | 检查方式 |
|---|---|---|
| 系统 | Ubuntu 26.04，x86_64 | `cat /etc/os-release`、`uname -m` |
| C++ 与 CUDA 主机编译器 | 同一个 `/usr/bin/g++`；该系统默认 GCC 15 系列 | `/usr/bin/g++ --version` |
| CUDA Toolkit | 13.4 系列 | `/usr/local/cuda-13.4/bin/nvcc --version` |
| GPU 代码 | `CMAKE_CUDA_ARCHITECTURES=89-real` | 构建目录中的 `build-info.txt` |
| 数学库 | 系统仓库的 `libntl-dev`、`libgmp-dev` | CMake 的链接及运行探针 |
| NVIDIA 驱动 | Ubuntu 为该笔记本提供的、兼容 CUDA 13.x 的驱动 | `nvidia-smi` 和 `zkp_device_info` |

NVIDIA 的 CUDA 13.4 支持表列出 Ubuntu 26.04、GCC 15.2，并允许 GCC 6–16；RTX 4060 的计算能力为 8.9。CUDA 13.x 的小版本兼容最低驱动系列为 R580，但仍有功能及 PTX 限制；因此项目生成 `sm_89` 的 SASS，并通过真实内核检测最终运行状态。参考 [Linux 安装支持表](https://docs.nvidia.com/cuda/cuda-installation-guide-linux/)、[GPU 计算能力表](https://developer.nvidia.com/cuda/gpus)、[驱动小版本兼容说明](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html)。查询日期：2026-09-16。

`nvidia-smi` 顶部的 “CUDA Version” 表示驱动可支持的 CUDA 版本信息，不是已安装的 Toolkit 版本。判断编译器要看 `nvcc --version`；不能仅因两个显示数字不同就认定无法运行。

## 安装依赖

```bash
sudo apt update
sudo apt install -y build-essential cmake python3 libntl-dev libgmp-dev
```

如果 `nvidia-smi` 已正确列出 RTX 4060，不必为编译项目重装显示驱动。如果它失败，先在 Ubuntu 的“软件与更新 → 附加驱动”中检查适用于本机的驱动；安装后按系统提示完成重启和 Secure Boot 密钥登记。驱动安装参考 [NVIDIA Ubuntu 指南](https://docs.nvidia.com/datacenter/tesla/driver-installation-guide/ubuntu.html)。项目脚本不修改驱动或系统库。

未安装 Toolkit 时，从 [NVIDIA CUDA 下载页面](https://developer.nvidia.com/cuda-downloads) 选择 Linux / x86_64 / Ubuntu / 26.04 / deb(network)，按照页面步骤启用对应仓库，然后安装 `cuda-toolkit-13-4`。已配置 NVIDIA 对应仓库的机器可以运行：

```bash
sudo apt update
sudo apt install cuda-toolkit-13-4
/usr/local/cuda-13.4/bin/nvcc --version
nvidia-smi
```

Toolkit 与驱动分别管理。不要把 Ubuntu 24.04 的 CUDA 软件源添加到 26.04，也不要安装旧的 `nvidia-340`、`nvidia-390`。不要为了 nvcc 强行把全系统 GCC 降为 13；选择支持系统编译器的 Toolkit。

## 构建和验收

解压后进入 `ZKP-Paillier-SP24-GPU2` 根目录：

```bash
python3 scripts/build.py --backend cuda
python3 scripts/validate_gpu.py
```

构建脚本默认选择 `/usr/bin/g++`，同时作为 C++ 和 nvcc 的主机编译器。CUDA Runtime 静态链接，无需专门为 libcudart 设置 `LD_LIBRARY_PATH`；NVIDIA 驱动仍由系统提供。nvcc 的查找顺序是显式 `--nvcc`、`CUDACXX`、`/usr/local/cuda-13.4/bin/nvcc`、PATH、`/usr/local/cuda/bin/nvcc`。若历史环境变量指向旧 Toolkit，请显式指定：

```bash
python3 scripts/build.py --backend cuda --cxx /usr/bin/g++ \
  --nvcc /usr/local/cuda-13.4/bin/nvcc --fresh
```

`--fresh` 将已有的 CMake 构建目录改名留存后重新配置，不删除原源码。默认构建目录是 `build-rtx4060`，默认编译并发数为 4，可用 `--jobs` 调整。CPU 单独构建：

```bash
python3 scripts/build.py --backend cpu
ctest --test-dir build-cpu --output-on-failure
```

GPU 验收依次检查真实内核、256–8192 位大整数、GPU 与 GMP 的结果、CPU/CUDA/混合模式的证明记录一致性、篡改拒绝，以及小规模和指定参数的完整证明。每个 CUDA/混合协议案例都必须在 **Encrypt 和 Commit 两个阶段**实际完成 GPU 任务，发生回退或返回 77 都不能通过。成功标志是 `GPU_VALIDATION=PASS`；日志目录中的 `report.json` 和 `environment.json` 可用于复查。

默认附加协议测试使用 512 位参数，仅用于功能检查。按自己的实验参数再次验收，例如：

```bash
python3 scripts/validate_gpu.py --bits 1024 --messages 40 --threads 16
python3 scripts/validate_gpu.py --bits 2048 --messages 10 --threads 16
```

可选的显存访问检查需要 Toolkit 中的 `compute-sanitizer` 位于 PATH：

```bash
PATH=/usr/local/cuda-13.4/bin:$PATH python3 scripts/validate_gpu.py --sanitizer
```

默认单项超时 1,800 秒；大参数或 sanitizer 下可以通过 `--timeout` 延长。功能验收启用了逐项 CPU 复算，不能用其耗时作为 GPU 加速比。

## 笔记本运行

```bash
./build-rtx4060/zkp_bench --backend hybrid --bits 1024 --messages 100 \
  --threads 16 --gpu-memory-mib 256 --verify-gpu
```

默认 256 MiB 是项目显式 GPU 缓冲区预算，并非整进程显存上限；CUDA 上下文与设备局部存储另占空间。任务会按可用显存分块。首次保留默认预算即可；较大预算是否更快需实测。`--device` 使用 CUDA 可见设备编号，不是 PCI 编号；若设置了 `CUDA_VISIBLE_DEVICES`，编号会重新排列。

笔记本应在同一供电和性能模式下比较 CPU/CUDA/混合模式。独显无需被设为桌面的主显示卡，只要 CUDA 可以访问它即可。VMware 普通虚拟显示适配器不能执行这套 CUDA 内核；真机 Ubuntu 中应能检测到 NVIDIA 设备。

## 针对既往报错的诊断

```bash
python3 scripts/diagnose.py --output environment.json
./build-rtx4060/zkp_device_info --device 0
```

`diagnose.py` 只读取配置并在临时目录编译一个数学库探针；输出文件要求不存在。未构建时也可以运行，但 CUDA 内核检查会注明缺少程序。要求内核确实通过时使用 `--require-gpu`。

| 现象 | 检查与处理 |
|---|---|
| `__cxa_call_terminate@CXXABI_1.3.15` 或 `GLIBCXX_* not found` | 看报告中实际加载的 libstdc++、NTL 路径。退出夹带旧运行库的环境，修正 `LIBRARY_PATH` / `LD_LIBRARY_PATH`，使用同一个系统 g++ 并重新配置。不要替换系统 `.so` 链接。CMake 会在完整构建之前报告探针失败。 |
| `unsupported GNU version` | nvcc 与 GCC 不匹配。Ubuntu 26.04 使用 CUDA 13.4 与系统 g++；不使用 `--allow-unsupported-compiler` 掩盖版本检查。 |
| `provided PTX was compiled with an unsupported toolchain` / 错误 222 | 确认构建目录是新版本，架构为 `89-real`，未设置 `CUDA_FORCE_PTX_JIT=1`，再检查驱动。默认二进制只有 sm_89 原生代码。 |
| `no kernel image is available` | GPU 不是编译目标或仍在运行旧产物；RTX 4060 用 `89-real` 重新构建。换其他 GPU 时用 `--arch` 显式指定。 |
| `no CUDA-capable device` / `insufficient driver` | 先检查 `nvidia-smi`、驱动加载、Secure Boot、CUDA_VISIBLE_DEVICES；设备不可见时构建成功也不能执行 GPU。 |
| CTest 显示 `Skipped` | 只表示未执行 GPU 测试；运行严格验收脚本，它将跳过视为失败。 |
| `hybrid` 输出 `reason=...` | 已触发 CPU 回退。检查原因；测试 GPU 性能前必须先通过严格验收。 |
| GPU 内存不足 | 降低消息量或 `--gpu-memory-mib`，关闭占用大量显存的其他程序；可用显存不足以容纳最小任务时应使用 CPU 模式。 |

性能测量、参数单位与协议保留范围见根目录 README。不能把“编译成功”当作已经完成 RTX 4060 实机正确性或性能验证。
