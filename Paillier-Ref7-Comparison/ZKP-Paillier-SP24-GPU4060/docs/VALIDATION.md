# GPU2 验证记录

验证日期：2026-09-16。CPU/OpenMP 路径、CUDA 编译链接、工具链检查与错误处理通过以下验证。**没有可用 NVIDIA GPU；GPU 内核运算、显存访问及加速比未完成实机验证。** Ubuntu 26.04 / RTX 4060 是适配目标，不能把下表的工具链编译验证表述为该笔记本已经通过验收。

## 实际测试环境

| 项目 | 实际环境 |
|---|---|
| 宿主用户空间 | Ubuntu 24.04.3，x86_64 |
| CPU 编译器 | GCC 13.3.0；另测试 GCC 15.2.0（Ubuntu `15.2.0-16ubuntu1` 软件包提取的工具链） |
| GCC 15 配套汇编器 | GNU binutils 2.46，来自 Ubuntu 软件包 |
| CMake | 4.4.3 |
| NTL / GMP | NTL 11.5.1 / GMP 6.3.0 |
| CUDA Toolkit | 13.4.1 组件集合；nvcc 13.4.59；Runtime 13.4.49 |
| CUDA 编译目标 | `89-real`；cuobjdump 确认包含 `sm_89.cubin`，不含 PTX |
| CUDA Runtime 链接 | 静态；诊断程序无需 libcudart 动态库路径即可启动 |
| GPU / 驱动 | 当前执行环境没有可用 NVIDIA GPU；内核探针返回 `cudaErrorInsufficientDriver (35)` |

GCC 15 的验证使用上述 GCC/binutils/C++ 运行库与现有 NTL/GMP，在 Ubuntu 24.04 宿主执行。未使用 Ubuntu 26.04 的完整系统、glibc 2.43、内核和 NVIDIA 驱动，因此它证明了这一编译器组合可以编译本项目，不能代替 Ubuntu 26.04 真机测试。

## 已执行检查

| 检查 | 结果 |
|---|---|
| 不带 CUDA 的 CPU 构建 | 通过 |
| GCC 13.3 + CUDA 13.4，全部 9 种大整数存储位宽 | 编译、链接通过 |
| GCC 15.2 + CUDA 13.4，全部 9 种大整数存储位宽 | 编译、链接通过 |
| 最终 GCC 15 构建的 CTest | 3 项通过，1 项 GPU 测试跳过；跳过不等于通过 |
| 主机数学模型 | 256–8192 位打包、Montgomery 补齐、窗口/桶算法与独立 GMP `mpz_powm` 对照通过 |
| 调度 | CPU/GPU 模拟工作者重叠、任务恰好执行一次、部分批次失败重算、严格模式、异常传播通过 |
| 协议 | CPU 1/3 线程固定输入一致；完整证明通过；篡改证明被拒绝 |
| 原 v6 对照 | 新版 GCC 13 和 GCC 15 的 5,886 字节公开证明记录均与 v6 基线逐字节相同 |
| 编译器混用 | 故意指定不同 CUDA 主机编译器，在配置阶段拒绝 |
| 数学库错误 | 故意设置错误 NTL 库路径，在配置阶段给出 NTL/GMP 链接诊断 |
| 无 GPU 时混合运行 | 保留输入，回退 CPU，完整证明通过，日志明确 `gpu=0` 及原因 |
| 无 GPU 时严格运行 / 实机验收 | 返回失败，不输出 GPU 验收成功标记；保存失败报告 |
| GPU 验收防误报 | 7 个测试通过：缺少证明、无 GPU 工作、阶段缺失、回退等均被拒绝 |
| 性能脚本 CPU 功能检查 | 生成日志、runs.csv、summary.csv；仅验证脚本，不视为性能实验 |

GPU 正确性程序包含实际 CGBN 内核与 GMP 的逐项对照、完整 CUDA/混合模式证明记录比较、最大位宽和边界输入测试，但设备分支尚未执行。`compute-sanitizer` 也未在实机运行。

## 可核对的记录

`docs/validation/` 保存构建、CTest、无驱动探针、原生 GPU 架构和证明记录摘要。日志中的本次临时工作目录统一替换为 `<workspace>`；未附带编译器或预编译可执行文件。

公开证明记录 SHA-256：

```text
5a5b3bdfcd766808842179bd950455769944facf94561f6df746a735f5072067
```

本次附件 CPU v6 ZIP 的 SHA-256：

```text
03856c536ce9a687f1ebac37813fb1b340e2518e29a65bee244818186c737a98
```

附件论文的 SHA-256：

```text
1b3db93cea9ecf5f53152b921d9cd57503339c0374d3824e5d50187ab66ab13d
```

v6 原 `src/cpp/app` 下 28 个 `.cpp/.hpp` 文件中，26 个与附件逐字节相同；仅 `CBatchEnc.cpp` 与 `PolynomialCommitment.cpp` 接入加速调度和边界检查。新增的 GPU/调度代码位于 `app/acceleration`，随机数采样、挑战、电路、验证器和证明格式沿用原实现。v6 对照使用 `tests/v6_transcript_fixture.cpp`，固定随机数仅限测试。

## 在目标笔记本完成验收

```bash
python3 scripts/build.py --backend cuda
python3 scripts/validate_gpu.py --bits 1024 --messages 40 --threads 16
```

验收通过后，再根据实际实验参数执行 CPU、CUDA 和混合模式的性能脚本。研究论文中的 CPU 加速数字未当作 GPU 成绩；本交付不包含虚构的 GPU 性能数据。
