# ZKP-Paillier-SP24-GPU2

基于上传的 `ZKP-Paillier-SP24-parallel-v6` 增加 NVIDIA CUDA GPU 后端，保留 CPU/OpenMP 路径，并支持 CPU 与 GPU 同时处理独立任务。

GPU2 已完成 CPU 构建、GCC 13.3 / GCC 15.2 + CUDA 13.4 的 sm_89 编译链接、CPU 正确性和原 v6 对照验证。交付环境没有可用 NVIDIA GPU，因此 CUDA 内核的实机正确性、显存检查和加速比尚待实机验证。请先运行下方 GPU 正确性测试，再采集性能数据。当前构建测试平台是 Ubuntu 24.04；Ubuntu 26.04 真机与 RTX 4060 的最终验证需要在你的笔记本执行。详细记录见 [验证记录](docs/VALIDATION.md)。

## 并行化范围

| 阶段 | CPU | GPU / CPU+GPU |
|---|---|---|
| 普通消息密文 `Cm` | OpenMP + GMP 按消息计算 | CUDA 批量计算 `r^n mod n²` 及最终模乘；混合模式共享消息队列 |
| `Commit(A/B/C)` | 保留 v6 的行级 GMP 桶算法 | 按“行 × 8 位窗口”分配 GPU 任务，CUDA 内合并每行结果 |
| 多项式承诺矩阵 `T` | 同上 | 复用矩阵 Commit 后端 |
| 单向量承诺 `D`、验证端单向量运算 | 保留原 NTL 实现 | 当前仍在 CPU 计算 |
| 辅助密文 `CRj`、`Cm_`、随机数、挑战和电路 | 保留原计算顺序 | 不进入 GPU 工作线程 |

GPU 计算使用随包提供的 [NVIDIA CGBN](https://github.com/NVlabs/CGBN) 大整数库，无需另行下载 CGBN 或 Google Test。接口、证明格式和验证逻辑沿用 v6；不更改密钥、生成元或随机数的采样方法。数学实现见 [GPU 实现说明](docs/GPU_IMPLEMENTATION.md)。

## RTX 4060 Laptop / Ubuntu 26.04 快速开始

目标配置为 Ubuntu 26.04 x86_64、RTX 4060（计算能力 8.9）、CUDA Toolkit 13.4、同一个系统 `/usr/bin/g++` 编译 CPU 和 CUDA 主机代码。详细安装与既往 ABI/PTX 报错处理见 [Ubuntu 26.04 / RTX 4060 指南](docs/UBUNTU_26_RTX4060.md)。

先安装 CPU 依赖，并确认 NVIDIA 驱动与 CUDA Toolkit 可用：

```bash
sudo apt update
sudo apt install -y build-essential cmake python3 libntl-dev libgmp-dev
nvidia-smi
/usr/local/cuda-13.4/bin/nvcc --version
```

进入项目根目录，执行：

```bash
python3 scripts/build.py --backend cuda
python3 scripts/validate_gpu.py
```

构建脚本使用独立目录 `build-rtx4060`，默认生成 `89-real` 原生机器码，不依赖 PTX 即时编译。CMake 在正式构建前检测 NTL/GMP 与当前 C++ 运行库的链接和执行是否兼容。无需下载 CGBN 或 Google Test。

成功验收必须看到 `GPU_VALIDATION=PASS`。没有 GPU、CUDA 测试跳过、回退 CPU、证明失败或 GPU/GMP 结果不一致都不能通过验收。验收报告和原始日志写入新建的 `validation-*` 目录。

显式选择工具链或从旧缓存重新构建：

```bash
python3 scripts/build.py --backend cuda --cxx /usr/bin/g++ \
  --nvcc /usr/local/cuda-13.4/bin/nvcc --fresh
```

只构建保留的 CPU/OpenMP 版本：

```bash
python3 scripts/build.py --backend cpu
ctest --test-dir build-cpu --output-on-failure
```

仍可直接使用 CMake（最低 3.20）：

```bash
cmake -S . -B build-rtx4060 -DZKP_ENABLE_CUDA=ON \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++ \
  -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++ \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-13.4/bin/nvcc \
  -DCMAKE_CUDA_ARCHITECTURES=89-real
cmake --build build-rtx4060 --parallel 4
```

其他 NVIDIA GPU 需选择正确架构，例如 `--arch 86-real`。默认 `89-real` 是 RTX 4060 定向构建；不包含用于其他架构的通用 PTX。`make rtx4060` 对应笔记本构建脚本；原 `make CUDA=ON` 以及 `src/cpp`、`test/cpp` 的兼容入口也保留。

## 首次在 GPU 上验证

```bash
./build-rtx4060/acceleration_tests --cuda
./build-rtx4060/zkp_bench --backend cuda --bits 64 --messages 3 --range-proofs 3 --threads 4 --verify-gpu
./build-rtx4060/zkp_bench --backend hybrid --bits 64 --messages 3 --range-proofs 3 --threads 4 --verify-gpu
```

`64` 位参数只用于快速功能测试。`--cuda` 测试会比较大整数计算、完整证明和 CPU/GPU 证明记录，并检查篡改后的证明不能通过。没有设备时它打印 `SKIP CUDA` 并返回 **77**；CTest 会显示 `Skipped`，这不代表 GPU 测试通过。

在目标参数下再检查一次，例如：

```bash
./build-rtx4060/zkp_bench --backend hybrid --bits 1024 --messages 10 --threads 16 --verify-gpu
```

`--verify-gpu` 对所有 GPU 结果逐项使用 CPU/GMP 复算。发现不一致会立即失败，不会把数值错误掩盖为成功回退。此选项会增加 CPU 工作量，测加速比时应关闭。

## 运行与性能测试

```bash
# CPU 基线
./build-rtx4060/zkp_bench --backend cpu --bits 1024 --messages 100 --threads 16 --repeat 3 --output cpu.csv

# 并行阶段使用 GPU，GPU 不可用或失败时直接报错
./build-rtx4060/zkp_bench --backend cuda --bits 1024 --messages 100 --threads 16 --repeat 3 --output cuda.csv

# CPU 与 GPU 共同处理任务
./build-rtx4060/zkp_bench --backend hybrid --bits 1024 --messages 100 --threads 16 --repeat 3 --output hybrid.csv
```

`--bits 1024` 表示 **1024 位 = 128 字节**，等价于原测试源码中的 `byteLength=128`。`--bits 2048` 对应 `byteLength=256`。不要把旧变量的字节数直接当作 `--bits` 的数值。

参数默认值为 `--bits 512 --messages 10 --range-proofs 128 --slot-bytes 4 --batch 15 --repeat 1`。输出 CSV 沿用原 end-to-end 字段，已有同名文件会报错以避免覆盖。运行后必须看到 `VERIFICATION=PASS`。`WALL_SECONDS` 是实际端到端墙钟时间，包含本次协议运行，不含进程启动和最初密钥生成。

自动比较多个消息规模：

```bash
python3 scripts/benchmark_backends.py --binary build-rtx4060/zkp_bench --bits 1024 --messages 1 10 40 100 --threads 16 --repeat 3 --output benchmark-results
```

脚本记录每次墙钟时间、Commit/Encrypt 阶段时间、GPU 任务数和原始日志，并按同一消息规模的 CPU 中位数计算加速比。若 `cuda` 或 `hybrid` 没有实际完成 GPU 任务，脚本会停止，防止把回退后的 CPU 时间当作 GPU 成绩。每次启动生成新的随机参数；相同输入、相同随机数下的逐字节等价由正确性测试单独验证。

## 模式和调度

| 模式 | 行为 |
|---|---|
| `cpu` | 使用 CPU，完全不查询或初始化 CUDA |
| `cuda` / `gpu` | 上表中支持卸载的并行阶段交给 GPU；设备缺失、位宽不支持、CUDA 错误直接报错 |
| `hybrid` | 一个 GPU 管理线程与 CPU/OpenMP 工作者共同领取任务；设备不可用时回退 CPU |
| `auto` | 当前与 `hybrid` 使用同一策略，自动处理设备不可用的情况；不代表自动选择了最快模式 |

混合模式使用原子任务队列，CPU 每次领取一行/一条消息。GPU 首批默认领取 1 项，之后根据实际批次耗时调整批量，默认上限 32 项，目标约 50 ms。不同阶段、不同调用单独测量。没有人为设定 CPU/GPU 速度比，也没有每轮全体 barrier。

`--threads 16` 在 CPU 模式对应最多 16 个 OpenMP 工作者；在混合模式对应最多 15 个 CPU 工作者加 1 个 GPU 管理线程。线程预算为 1 时，仍使用一个 CPU 调用线程和一个 GPU 管理线程。

日志示例字段：

```text
[Commit] requested=hybrid cpu=... gpu=... fallback=... wall_ms=... gpu_batch_ms=... kernel_ms=... check_ms=...
```

`gpu_batch_ms` 包括 GPU 批次的序列化、准备、传输与执行等待，`kernel_ms` 由 CUDA event 计时。`wall_ms` 是本次任务调度的墙钟时间；完整 Commit 时间还包含外层 NTL 转换。CPU/GPU 可以重叠，不能把这些字段简单相加得到端到端时间。

## 可配置项

| 环境变量 | 默认值 | 对应命令行 / 说明 |
|---|---:|---|
| `ZKP_BACKEND` | `auto` | `--backend` |
| `ZKP_CPU_THREADS` | `0` | `--threads`；0 使用 OpenMP 默认值，可用 `OMP_NUM_THREADS` 控制 |
| `ZKP_CUDA_DEVICE` | `0` | `--device`；按 `CUDA_VISIBLE_DEVICES` 过滤后的编号 |
| `ZKP_GPU_CHUNK` | `1` | `--gpu-chunk` |
| `ZKP_GPU_MAX_CHUNK` | `32` | `--gpu-max-chunk` |
| `ZKP_GPU_MEMORY_MIB` | `256` | `--gpu-memory-mib`；显式 GPU 数据缓冲区预算，至少 8 MiB |
| `ZKP_GPU_VERIFY` | `0` | `--verify-gpu` 可启用；1 逐项对照 |
| `ZKP_ACCEL_VERBOSE` | `1` | `--quiet` 可关闭调度摘要 |

CPU 与 CUDA 都使用任意精度/多精度整数。CUDA 存储位宽为 256、512、1024、1536、2048、3072、4096、6144、8192 位，按**实际模数**选择。Commit 的 `Q=f*n²+1` 通常比 `n²` 多几位，不能直接按 Paillier 的 `n` 位宽选取；例如 2048 位 `n` 的 Commit 通常需要 6144 位存储。

超过支持位宽或显存预算时，`auto/hybrid` 保留原输入并回退 CPU，`cuda` 报错。4096 位 `n` 的 `n²` 可以落在 8192 位内，但 Commit 的 `Q` 可能超过上限，需要回退。GPU 缓冲预算不包含 CUDA 上下文、驱动开销与编译器生成的设备局部存储，也不会限制 NTL/GMP 的主机内存。

这是一份研究实现。GPU 支持不等于保证加速；小批量、主机序列化和大整数位宽补齐都可能抵消收益。协议参数生成、原始 v6 随机状态管理和非恒定时间算法沿用原项目；本次不构成生产密码学或侧信道安全审计。

## 文件入口

- `scripts/build.py`：统一主机编译器、独立构建目录、RTX 4060 架构。
- `scripts/diagnose.py`、`tools/device_info.cu`：环境、库路径与真实 CUDA 内核诊断。
- `scripts/validate_gpu.py`：严格实机验收与日志报告。
- `src/cpp/app/acceleration/CudaBackend.cu`：GPU 内核、显存分块和 CUDA 会话。
- `src/cpp/app/acceleration/Backend.cpp`：模式、共享任务队列、回退与统计。
- `src/cpp/app/acceleration/WindowMath.hpp`：GPU 与主机模型共用的 8 位桶/合并算法。
- `src/cpp/app/PolynomialCommitment.cpp`、`CBatchEnc.cpp`：与原协议的连接位置。
- `tests/acceleration_tests.cpp`：独立 GMP 对照、调度异常、证明记录一致性和 GPU 实机测试。
- `docs/legacy/`：原 CPU 版本说明与实验文档；其中性能数字不是本 GPU 版本的实验结果。
