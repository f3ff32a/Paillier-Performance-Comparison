# 用户项目的共同加密算子适配器

`paillier_compare.cpp` 复用所提供用户项目的 `Backend.cpp` 调度器及 `CudaBackend.cu` GPU 加密实现，仅提取普通 Paillier 加密 `c=(1+n*m)*r^n mod n²`；不涉及原项目的零知识证明协议修改。

## 推荐：独立构建（不需要 NTL）

在整个比较包的根目录执行，Linux CPU 依赖为 CMake、C++ 编译器、OpenMP 与 GMP。GPU 另需 NVIDIA 驱动、CUDA Toolkit 和原项目随附的 CGBN。

```bash
cmake -S zkp_adapter -B build-zkp-compare-cpu -DZKP_COMPARE_CUDA=OFF
cmake --build build-zkp-compare-cpu --parallel 4
python3 zkp_adapter/test_adapter.py --binary build-zkp-compare-cpu/paillier_compare
```

RTX 4060 GPU 构建示例（CUDA 路径按目标机器实际安装调整）：

```bash
cmake -S zkp_adapter -B build-zkp-compare-cuda -DZKP_COMPARE_CUDA=ON \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++ \
  -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++ \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-13.4/bin/nvcc \
  -DCMAKE_CUDA_ARCHITECTURES=89-real
cmake --build build-zkp-compare-cuda --parallel 4
python3 zkp_adapter/test_adapter.py --binary build-zkp-compare-cuda/paillier_compare --gpu
```

默认源目录为相邻的 `ZKP-Paillier-SP24-GPU4060/`。其他目录可加 `-DZKP_SOURCE_DIR=/absolute/path/to/project`。使用目标主机实际支持的编译器与 CUDA 版本；CPU 与 CUDA host 编译器保持一致。

## 也可接入完整原项目

```bash
python3 zkp_adapter/install_adapter.py /absolute/path/to/ZKP-Paillier-SP24-GPU4060
cd /absolute/path/to/ZKP-Paillier-SP24-GPU4060
python3 scripts/build.py --backend cuda
cmake --build build-rtx4060 --target paillier_compare --parallel 4
```

安装器仅复制 `tools/paillier_compare.cpp` 与 `cmake/PaillierComparison.cmake`，在根 CMake 增加 include，并生成 `CMakeLists.txt.before-paillier-comparison` 备份。它不修改协议源码，遇到不同的已有适配器文件会拒绝覆盖。完整项目构建仍需要原来的 NTL 依赖。

## 数据合同与运行

JSON 字段为 `schema="paillier-comparison-v1"`、`bits`、`n`、`n2`、`messages`、`randomness`、`expected`、`dataset_id`。所有大整数为规范十进制字符串；`bits` 必须等于 `n` 的实际位长；`n2=n*n`；消息在 `[0,n)`；随机数在 `(0,n)` 且与 `n` 互素。`expected` 数组必须与独立 GMP 计算逐项一致。

`dataset_id` 的合同是对核心七字段 `{schema,bits,n,n2,messages,randomness,expected}` 做 JSON 的 `sort_keys=True,separators=(',',':'),ensure_ascii=True` 规范序列化，再取 ASCII 字节的 SHA-256。C++ 程序检查其格式并原样输出，不重新计算指纹；统一实验脚本负责确认输入的实际指纹。额外 metadata 不参与该指纹。

```bash
./build-zkp-compare-cpu/paillier_compare --dataset datasets/example.json --check-only
./build-zkp-compare-cuda/paillier_compare --dataset datasets/example.json \
  --backend cpu --threads 16 --warmup 1 --repeat 5 \
  --output results/user-cpu.csv --results-output results/user-cpu-ciphertexts.json
./build-zkp-compare-cuda/paillier_compare --dataset datasets/example.json \
  --backend cuda --threads 16 --warmup 1 --repeat 5 \
  --output results/user-cuda.csv --results-output results/user-cuda-ciphertexts.json
./build-zkp-compare-cuda/paillier_compare --dataset datasets/example.json \
  --backend hybrid --threads 16 --warmup 1 --repeat 5 \
  --output results/user-hybrid.csv --results-output results/user-hybrid-ciphertexts.json
```

输出目录需预先存在；已有结果文件拒绝覆盖。每次运行必须看到 `VERIFICATION=PASS` 和最终 `PASS`。CUDA 模式要求全部 N 个任务在 GPU 完成；hybrid 必须实际完成 GPU 任务。任何 GPU 回退都使本次比较失败。CPU 模式完全不查询 CUDA。

## 计时口径

CSV 的 `timing_scope` 固定为 `host-inclusive-session-per-run`。`host_ms` 包含每次结果数组分配、任务中的十进制/GMP 转换、调度线程、GPU session 创建、常量及数据传输、内核、输出转换与完整 session 销毁。文件读取、数据验证、密钥与随机数生成、独立 oracle 检查、CSV/JSON 写入不进入计时。

warmup 是额外预热运行，保留 `warmup=1` 的 CSV 行；正式统计排除这些行。正式运行也每次新建 session，预热并不复用设备常量；预热可减少 CUDA 环境及系统首次使用影响。`kernel_ms` 单独保留，由原 GPU 后端的 CUDA event 计时，不能与其他实现的 host-inclusive 时间混比。

列为 `dataset_id,implementation,backend,mode,modulus_bits,logical_count,ciphertext_count,run,warmup,host_ms,kernel_ms,verified,packing,threads,cpu_tasks,gpu_tasks,fallback_tasks,actual_n_bits,actual_n2_bits,timing_scope`。`implementation=zkp-paillier`、`mode=encryption`、`packing=none`，逻辑数等于密文数。线程预算复用原调度器：hybrid 保留一个 GPU 管理线程，至少一个 CPU 工作者；当预算为 1 时原调度器仍有 CPU 调用线程和 GPU 管理线程。

## 测试范围

`test_adapter.py` 使用小型数学测试模数 `17*19` 检验边界消息、随机数、每项密文、CSV/JSON、预热行以及错误输入拒绝。此模数只用于功能测试。`--gpu` 才执行 CUDA 和 hybrid 验证；仅运行 CPU 测试不证明真实 GPU 计算正确或有加速效果。正式 GPU 性能数据还应在论文参数和目标硬件上采集。
