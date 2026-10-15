# ZKP-Paillier-SP24-Serial-Ubuntu26

本项目从你上传的原作者串行源码修改，作为同机比较原串行程序、OpenMP 版本和 GPU 版本的基线。保留原作者的串行加密、桶式 Commit、多项式承诺、证明及验证流程。

**运行固定为串行 CPU，NTL 内部线程数为 1。** 你的 RTX 4060 笔记本及其他 Ubuntu 机器均使用各自 CPU 运行此基线，不需要显卡、CUDA、OpenMP、Google Test、CMake 或修改驱动。

## 1. 安装与编译（Ubuntu 26.04）

解压后，在本文件所在的项目根目录执行：

```bash
cd ZKP-Paillier-SP24-Serial-Ubuntu26
bash scripts/install_ubuntu.sh
python3 scripts/doctor.py
make -j2
```

安装脚本仅安装 Ubuntu 仓库中的 `build-essential python3 libntl-dev libgmp-dev`，首次安装可能需要管理员密码。未启用 universe 时会提示；现有第三方软件源报错时，先修复软件源再安装。`-j2` 只是并行编译两个源文件，**不会让实验程序并行**。

手动安装也可以：

```bash
sudo apt update
sudo apt install build-essential python3 libntl-dev libgmp-dev
make -j2
```

每台机器都在本机编译。包内不带预编译程序或 NTL/GMP 动态库，避免不同 Ubuntu / C++ ABI 混用。默认 `-std=c++17 -O3`，无覆盖率插桩、无 `-fopenmp`，不启用 `-march=native`。如果对比程序采用本机指令集优化，可以统一双方选项后用 `make -j2 NATIVE=1`。**更换编译器、依赖或选项后，先 `make clean`。**

## 2. 先做小检查

```bash
make check
python3 scripts/run_experiments.py --suite smoke --repeat 1
```

`make check` 检查完整证明、篡改拒绝、承诺算术、边界输入、批量输出、超时/中断/续跑，以及 MC=1 零聚合回归，不会运行完整的大参数实验。smoke 使用 64 bit，仅作功能测试。

512 bit、MC=1 的正式参数小检查：

```bash
python3 scripts/run_experiments.py --suite long512 --mc 1 --repeat 3 --out results/serial-check512
```

日志中应出现：

```text
backend=serial CPU_THREADS=1 OpenMP=OFF CUDA=OFF NTL_THREADS=1
VERIFICATION=PASS run=1 WALL_SECONDS=...
```

## 3. 对应此前实验的批量命令

**A：512 bit，MC=1–2000，三次重复**

```bash
python3 scripts/run_experiments.py --suite long512 --repeat 3 --out results/serial-A
```

MC 为 `1,10,20,40,80,100,200,400,800,1000,2000`，共 11 个配置，每个 MC 独立 CSV/log。

**B：256/512/1024/2048 bit，MC=1–400，三次重复**

```bash
python3 scripts/run_experiments.py --suite grid --repeat 3 --out results/serial-B
```

每个位数的 MC 为 `1,10,20,40,80,100,200,400`，共 32 个配置。256/512/1024 bit 各合并为一个 CSV/log；2048 bit 位于独立 `2048bit` 文件夹，按 MC 分文件。

**一条命令完成 A 和 B**

```bash
python3 scripts/run_experiments.py --suite both --repeat 3 --out results/serial-all
```

A/B 的 512 bit 数据分开保存，不混成六次重复。所有配置逐个运行，默认无超时，失败后继续下一个。大位数与大 MC 可能很慢或超过内存；源码兼容不能保证任意参数均完成。

只补跑 2048 bit：

```bash
python3 scripts/run_experiments.py --suite grid --bits 2048 --repeat 3 --out results/serial-2048
```

自选位数和 MC：

```bash
python3 scripts/run_experiments.py --suite grid --bits 256 512 --mc 1 100 400 --repeat 3 --out results/serial-custom
```

按 Ctrl+C 停止后，已完成结果会保留。使用同一输出目录及原重复数/种子续跑：

```bash
python3 scripts/run_experiments.py --suite both --repeat 3 --out results/serial-all --resume
```

续跑核对参数、可执行文件、运行环境指纹、CSV 行数与内容及 PASS 状态，只跳过完整成功的配置。失败/中断配置重新执行，旧尝试保留；换程序或依赖后使用新目录。

可选：每个配置限制 3600 秒，包含密钥生成和三次运行：

```bash
python3 scripts/run_experiments.py --suite both --repeat 3 --timeout 3600 --out results/serial-limited
```

达到上限记 TIMEOUT，不能据此断言机器绝对跑不动或测得无穷耗时。加 `--stop-on-error` 可在首个失败或超时后停止。

## 4. 输出与汇总

以 `results/serial-all` 为例：

```text
long512/serial_512bit_mc1.csv、.log …… mc2000
 grid/serial_256bit.csv、.log
 grid/serial_512bit.csv、.log
 grid/serial_1024bit.csv、.log
 grid/2048bit/serial_2048bit_mc1.csv、.log …… mc400
 summary.csv
 statuses.csv
 manifest.json
 environment.json
```

CSV 前 **35 列**保留原作者与现有 GPU 实验的列名和顺序。追加 7 列：`wall_seconds,verification,run_index,keygen_seconds,seed,execution_backend,ntl_threads`。

正式 CSV 仅合并达到请求重复数且全部 PASS 的配置。部分成功、失败和中断的原始证据仍保存在输出目录内部的运行记录中。`statuses.csv` 区分 PASS、FAIL、TIMEOUT、INTERRUPTED 等状态。`summary.csv` 对不完整配置保留状态、留空测量值，不写假的零或无穷数值；论文 Word 表格可按实际未完成原因标“∞”，并说明它是状态符号。

脚本自动更新 `summary.csv`，也可重算：

```bash
python3 scripts/summarize.py results/serial-all
```

汇总包含墙钟和阶段合计的中位数、Commit/Prove/Verify 中位数、每消息耗时、吞吐量、证明字节数/消息及样本 CV。CV = 样本标准差 ÷ 算术平均数 × 100%；不足 2 次时留空。阶段合计先逐次相加，再取中位数。

## 5. 不用批量脚本，直接运行 C++

无需改源码或重编译就能换参数：

```bash
mkdir -p results
./build/serial_benchmark --bits 512 --mc 100 --range 128 --slot 4 --batch 15 --repeat 3 --csv results/one512mc100.csv > results/one512mc100.log 2>&1
```

CSV 必须是新文件，不覆盖已有文件。支持原 GPU 参数别名 `--messages / --range-proofs / --slot-bytes / --output`；`--threads` 只允许 1，`--backend` 只接受 serial 或 cpu。

可选固定随机种子，仅用于可复现实验：

```bash
python3 scripts/run_experiments.py --suite grid --bits 512 --mc 1 10 --seed 20261012 --out results/serial-seeded
```

默认不固定种子。不同程序/依赖版本的相同数字种子不自动保证同输入，须实际核对密钥、电路和证明记录。

## 6. 如何做性能对比

- `--bits` 的单位是 **bit**：256/512/1024/2048 bit 对应 32/64/128/256 bytes。旧列名 `byte length(bit)` 保留以兼容历史数据。
- 默认参数与此前实验相同：range proofs=128、slot bytes=4、batch=15、repeat=3。每个 MC 配置生成一次密钥，三次运行复用密钥。
- `wall_seconds` 用 `steady_clock` 包围一次 `end_to_end(crypto,...)`，不包含外部密钥生成；调用内的参数生成、初始化、聚合、日志及清理计入墙钟。不要与整进程时间或 GPU 内核时间混用。
- 阶段计时保留原 Timer 的毫秒截断口径；`circuit create time` 为 P/V 构建时间之和除以 2。`phase1+phase2` 是 CSV 阶段合计，不是真实墙钟。`amo.*` 按消息数×槽数分摊，不是每消息墙钟。
- 本次修复了附属解包/平均值演示的越界、ASCII 求和、位序及整数除法错误；它不参与证明。修复后的 phase3 不应与旧错误演示的 phase3 作同算法比较。
- 串行加速比 = **同机、同参数串行墙钟中位数 ÷ 并行墙钟中位数**。阶段加速比使用对应阶段。建议同机重新跑两版，统一编译优化、依赖及供电状态，不同时启动其他实验；保留三次数据及 CV，不自动剔除慢轮次。
- 跨机 CPU、线程数、系统和电路规模可能不同，整机倍数不能直接归因于显卡。若 v6/GPU 版还包含算法/生命周期优化，总加速体现所有改动，不能只称为新增并行化的贡献。
- 256/512/1024/2048 bit 是已有实验的测试参数；本项目未据此评估生产部署安全等级。

## 7. 验证范围与排错

实际修复和验证范围见 [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md)，原作者说明见 [docs/README-author.md](docs/README-author.md)。`reference` 中有上传源码归档、修改清单和补丁，便于论文核查。

已经在提供的 Linux 测试环境完成编译和小规模功能验证；Ubuntu 26.04 和你的电脑需用 `make check` 及 512 bit 小检查确认实际运行。完整 43 配置的性能数据由你在目标机器测量，包内不填入模拟性能数据。

若头文件缺失、动态库找不到或出现 `CXXABI_1.3.15`：

```bash
python3 scripts/doctor.py
```

依据诊断核对编译器、系统 NTL/GMP 和实际加载的 libstdc++。留意旧 `/usr/local` 自编译库及 `LD_LIBRARY_PATH`，不要混用另一系统复制的动态库。诊断脚本只读检查，不删除库或改系统。
