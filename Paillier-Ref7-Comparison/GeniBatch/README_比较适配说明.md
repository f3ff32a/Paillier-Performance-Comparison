# 参考文献 7：GeniBatch 的 Paillier 比较适配

已获取作者公开仓库并保留其全部 18 个源文件，固定提交为 `bb80c4581373d7a779839482d256161fd33a7cab`。`upstream/` 的文件与下载版本逐字节相同，清单见 `UPSTREAM_SOURCE.json`。本适配没有改写 GeniBatch 的加密核心或批编码核心。

GeniBatch 是面向隐私保护机器学习的批编译和编码优化；用户项目还包含零知识证明。两者目前可以公平对比的是 **相同输入上的 Paillier 加密算子**，不能用 GeniBatch 的加密时间直接对比用户项目的完整证明生成或验证时间。

## 实际复用的程序

- `upstream/experiments/test_enc_dec.py` 的 CPU 路径调用 FATE `PaillierPublicKey.raw_encrypt`。公开仓库没有随附 FATE 本体。
- 为补齐该依赖，保留了官方 FATE `v1.8.0` 的 `fate_paillier.py`、`gmpy_math.py`、`fixedpoint.py` 和 Apache 2.0 许可证。版本提交为 `3ee02ea81c62d60353b2df40e141529b151d7c67`，下载地址和 SHA256 见 `vendor/fate-v1.8.0/SOURCE.json`。这三个源文件保持原样。
- 新增两个最小 `__init__.py` 仅用于 Python 包导入，避免启动完整 FATE 框架。新增适配入口在 `comparison/compare_paillier.py`，实现和说明在 `benchmark_adapter.py`。
- CPU 比较逐项调用原始 `raw_encrypt(m, random_value=r)`，计算 `c=(1+n*m)*r^n mod n²`。不另造 Paillier 算法，不执行内置的大规模机器学习实验。
- FATE 原始 `gmpy_math.powmod` 对较小操作数使用 Python `pow`，其余使用 `gmpy2.powmod`；大明文可能使用原始逆元捷径。报告保留这些行为，不能声称全部运算都走 GMP，也不能声称与用户 C++ 后端具有相同实现细节。

## CPU 共同算子比较

以下命令在整个比较包的根目录运行，使用 Python 3.11 或更新版本。依赖固定为 `gmpy2==2.3.0`、`numpy==2.3.5`。推荐单独虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r GeniBatch/requirements.txt

python scripts/generate_dataset.py --bits 1024 --messages 40 --profile zkp-slots --output datasets/input-1024-40.json
python GeniBatch/comparison/compare_paillier.py \
  --dataset datasets/input-1024-40.json --backend cpu \
  --repeat 5 --warmup 1 \
  --output results/genibatch-cpu-1024-40.csv \
  --results-output results/genibatch-cpu-1024-40.json
```

Windows 激活命令为 `.venv\Scripts\Activate.ps1`，生成和运行命令的参数相同。`--output`、`--results-output` 必须是尚不存在的不同路径，也不能与输入文件重合，避免覆盖原始数据。运行失败返回退出码 2；在新的、安全的 JSON 输出路径记录错误，不产生成功的 CSV 记录。

输入格式为统一 `paillier-comparison-v1` JSON，包括 `bits`、`n`、`n2`、十进制字符串数组 `messages`、`randomness`、`expected` 和 `dataset_id`。`dataset_id` 使用七个核心字段的规范 JSON SHA256。原始文件内容、数组长度、明文范围、随机数范围和互素性、每个期望密文及哈希均会检查；每次正式运行和预热也逐项比较实际密文。全通过才返回 0。

`implementation=genibatch-fate-cpu`，`mode=encryption`，`packing=none`，`threads=1`。此模式代表公开 GeniBatch 使用的 FATE 单进程加密基线，不代表 GeniBatch 整个编译器的加速效果。每条 CSV 记录表示一次完整运行；预热 `warmup=1`，正式运行 `warmup=0` 且编号从 1 开始。与用户适配器共享同一数据文件、密钥模数、随机数、消息范围、消息数量，才能进入共同算子汇总。

计时口径 `host-inclusive-session-per-run`：每次包含十进制输入转大整数、公钥对象构造、输出空间分配、加密计算和密文转十进制。文件读写、密钥生成、随机数生成、模块导入、参考答案计算和逐项校验在计时外。`host_ms` 为上述总时间；附加的 `compute_ms` 仅为原始 `raw_encrypt` 循环时间。CPU 的 `kernel_ms` 留空，不能填入伪造的 GPU 内核时间。这里的“session”指一次已加载程序的计时会话，不含进程启动或 Python 导入。

## 可选：原始整数槽批编码演示

此模式调用未修改的 `BatchEncoder.squeeze`，跳过浮点量化，用小非负整数展示一个密文容纳多条逻辑输入的效果。它没有复现浮点模型训练、编译器位预留计划、通信或完整 PPML 应用。

```bash
python scripts/generate_dataset.py --bits 1024 --messages 40 --profile small-integers --output datasets/input-packed-demo.json
python GeniBatch/comparison/compare_paillier.py \
  --dataset datasets/input-packed-demo.json --backend cpu \
  --mode packed-encryption --pack-size 6 \
  --data-bits 24 --sign-bits 40 --slot-bits 168 \
  --repeat 5 --warmup 1 \
  --output results/genibatch-packed-demo.csv \
  --results-output results/genibatch-packed-demo.json
```

最后不足一组的输入补零；每组采用该组首条输入的固定 `r`。例如 40 条逻辑输入、每组 6 槽，仅产生 7 个密文。JSON 明确保存槽分组、随机数选择和填充规则；CSV 的 `logical_count` 与 `ciphertext_count` 分别报告两种数量。该实验减少了加密和随机数操作数量，属于不同工作量。不能把它与同算子模式混入一个加速比；包内 `summarize.py` 会拒绝这样的输入。全域大整数消息不适合直接批编码，程序会拒绝超过槽容量的消息。

## GeniBatch GPU 的真实限制

原始 GPU 路径是 `FPN_store.encrypt(pub_key).obfuscation()`，依赖模块 `federatedml.FATE_Engine.python.bigintengine.gpu.gpu_store`。GeniBatch 论文实现节称其 GPU 路径采用 HAFLO。此次获取的公开 GeniBatch 仓库未包含 `FATE_Engine`、HAFLO 的 GPU 源码或构建系统，也没有记录如何注入逐条固定 `r`、同步计时或导出完整密文。

因此 `--backend cuda` 会明确失败。即使检测到同名模块，尚未建立并验证固定随机数和密文导出桥接前也会拒绝运行。程序不会把 CPU 结果、用户项目的 CUDA 后端或重写的 CUDA 算法标作 GeniBatch GPU。获取作者实际 GPU 依赖后，仍需对其输入转换、H2D、内核、同步、D2H 和随机数接口做独立适配验证。

## 本机验证范围

验证环境：Windows，bundled Python，NumPy 2.3.5，gmpy2 2.3.0 / GMP 6.3.0。`comparison/test_adapter.py` 的 12 项检查通过，覆盖边界明文、FATE 逆元分支、真实 CPU 加密、统一 CSV/JSON、逐项校验、规范哈希、非单位随机数、错误答案、整数槽还原、无 GPU 时失败、覆盖保护和源文件 SHA256；日志在 `validation/test_adapter.txt`。

```bash
python GeniBatch/comparison/test_adapter.py
```

这些检查是功能验证，未复现论文的大规模 PPML 实验或 GeniBatch GPU 性能；不构成用户 RTX4060 环境上的性能结论。整个包其他后端的验证以根目录验证报告为准。

## 来源

- 作者项目：https://github.com/Huangxy-Minel/GeniBatch
- 作者论文：https://www.cse.ust.hk/~kaichen/papers/genibatch-eurosys24.pdf
- 官方 FATE v1.8.0：https://github.com/FederatedAI/FATE/tree/v1.8.0
- 原始 FATE 加密文件：https://github.com/FederatedAI/FATE/blob/3ee02ea81c62d60353b2df40e141529b151d7c67/python/federatedml/secureprotol/fate_paillier.py
