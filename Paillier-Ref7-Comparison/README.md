# 参考文献7 GeniBatch 与 ZKP-Paillier 的共同算子对比程序

已获取作者主页链接的 GeniBatch 官方项目，并为双方增加同输入 Paillier 加密基准。
本包包含官方源码、FATE 1.8 CPU 依赖源码、你的项目源码、两个适配器、统一实验脚本和验证记录。
原始 DOCX 和 ZIP 未被修改。用户项目的协议实现保持原样，适配器是新增程序。

## 一、项目来源与对比范围

参考文献7：X. Huang et al., Accelerating Privacy-Preserving Machine Learning With GeniBatch,
EuroSys 2024, pp.489–504, DOI 10.1145/3627703.3629563。
作者主页：https://huangxy-minel.github.io/publications/
官方仓库：https://github.com/Huangxy-Minel/GeniBatch
固定版本：bb80c4581373d7a779839482d256161fd33a7cab。
FATE：v1.8.0，3ee02ea81c62d60353b2df40e141529b151d7c67。
公开仓库是作者提供的源码快照；没有据此声称完整复现论文系统或全部论文结果。

普通加密对比任务为：c_i = (1+n*m_i) * r_i^n mod n²。
双方读取同一个 JSON，使用相同 n、m_i、r_i，产生相同数量的真实密文，并逐项核对。
GeniBatch/FATE 基线使用原始 PaillierPublicKey.raw_encrypt，没有重写它的加密核心。
你的程序复用原 Backend.cpp、CudaBackend.cu、CGBN，以及 CBatchEnc.cpp 的 GMP 加密表达式。
你的 CPU 路径和 GeniBatch CPU 路径都可用一个工作线程测试；另测你的多线程、CUDA 和 hybrid。
线程数表示请求的工作者预算，实际并发还受OpenMP环境与任务量影响。
输出记录线程预算，并分别计算相对 GeniBatch 单线程 CPU、你的单线程/多线程 CPU 的加速比。
跨实现对比同时包含语言、绑定层、调度和计算的成本，不能直接解释为纯内核算法优势。

GeniBatch 没有你采用的零知识证明协议。其 Commit、Prove、Verify、证明大小及完整协议耗时
不存在同功能可比项，不能用本包的普通加密耗时替代这些指标。

官方公开仓库未包含其引用的 HAFLO/FATE_Engine GPU 底层，本次检索也未找到可获取的官方后端。
GeniBatch 的 --backend cuda 因此会明确失败，不会执行模拟GPU或用你的CUDA代码替代作者实现。
当前支持的比较是 GeniBatch/FATE CPU 与你的 CPU/CUDA/hybrid 加密算子。
“GeniBatch GPU vs 本项目 GPU”的独立比较仍需要取得作者 GPU 代码并实现固定随机数接口。

## 二、Ubuntu RTX 4060 快速运行

先将 ZIP 解压，进入 Paillier-Ref7-Comparison 根目录。
以下依赖安装命令需要在你准备运行实验的 Ubuntu 环境自行执行：

```bash
sudo apt update
sudo apt install -y build-essential cmake python3-venv libgmp-dev
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r GeniBatch/requirements.txt
```

独立基准不需要 NTL。完整原零知识证明程序仍需原来的 NTL 等依赖。
CUDA 构建要求本机 NVIDIA 驱动与 CUDA Toolkit 可用，并沿用你已有的同一 C++ 主机编译器。

```bash
nvidia-smi
python scripts/build_compare.py --backend cuda --cxx /usr/bin/g++ \
  --nvcc /usr/local/cuda-13.4/bin/nvcc --arch 89-real --test
```

CUDA 安装目录不同，请修改 --nvcc。测试必须实际通过，缺GPU或CPU回退均不会作为GPU成功。
独立GPU验收只针对本包提取的加密算子，不能替代原项目 scripts/validate_gpu.py 的完整协议验收。

先跑小规模烟雾测试：

```bash
python scripts/run_comparison.py --zkp-binary build-zkp-compare-cuda/paillier_compare \
  --bits 256 --messages 1 10 --threads 16 --repeat 3 --warmup 1 --output results-smoke
```

再跑与你论文规模对应的实验：

```bash
python scripts/run_comparison.py --zkp-binary build-zkp-compare-cuda/paillier_compare \
  --bits 256 512 1024 --messages 1 10 40 100 200 400 --profile zkp-slots \
  --threads 16 --repeat 5 --warmup 1 --output results-main
```

程序默认同时跑 GeniBatch/FATE 单线程 CPU，以及你的 CPU 1线程、CPU 16线程、CUDA、hybrid。
--cpu-threads 可单独指定你的CPU预算，例如 --cpu-threads 1 4 8 16。
扩展至 --bits 2048、--messages 2000 时，请根据机器容量与运行时间分开采集。
结果目录必须是新目录。程序停止时看 failure.json 和对应 .log，不得将失败前残留CSV当完整实验。
summary.json 保存中位数、吞吐量、样本数与明确指定基线的加速比。
environment.json、逐次CSV、最终密文JSON、原始日志和输入数据均保留，便于复核。
--zkp-binary 必须指定新程序 paillier_compare，不能指向原端到端 zkp_bench。

仅用CPU也可完整验证流程：

```bash
python scripts/build_compare.py --backend cpu --cxx /usr/bin/g++ --test
python scripts/run_comparison.py --zkp-binary build-zkp-compare-cpu/paillier_compare \
  --zkp-backends cpu --bits 512 --messages 1 10 100 --threads 16 \
  --repeat 5 --warmup 1 --output results-cpu
```

## 三、数据与计时口径

--profile zkp-slots 使用原项目默认4字节槽、每槽0/1的消息构造形态，仅提取普通Cm消息。
--profile full-domain 包含0、1、n-1边界及全域随机明文，用于功能/参数覆盖。
--profile small-integers 使用16位非负整数，适合额外打包演示。
所有输入的随机数满足0<r<n、gcd(r,n)=1。密钥与随机数生成均在正式计时外。
每份数据采用实际达到指定整数位长的 n；CSV另记实际 n² 位长，n² 的位宽约为 n 的两倍。
小位宽沿用论文的性能研究参数；此处数据与密钥仅用于公开基准。

host_ms 计主机可见的每次输入/输出表示转换、结果分配、实现自身调度与运算。
你的GPU路径还计session创建、H2D、内核、D2H及session销毁；设备探测在计时外。
正式运行每次新建session，warmup为额外预热，不代表持续复用GPU缓冲。
读取/写入文件、oracle复算和密文比较在host_ms外。
kernel_ms独立记录CUDA event内核时间，不能与另一实现host_ms直接混比。
只比较同 dataset_id、同密文数、同位宽、同计时范围、verified通过且无回退的行。
数据指纹是核心七字段规范JSON的SHA256，额外metadata不参与；统一runner实际重算。
C++读取程序只验证指纹格式并原样输出，因此推荐使用统一runner。
FATE保留原始实现对大明文的逆元分支，以及gmpy_math自身的小数值Python pow分支。

## 四、GeniBatch整数槽打包演示

该模式调用原始 BatchEncoder.squeeze；绕开浮点量化，使用预量化非负整数。
它展示打包减少真实密文数量，尚未移植完整PPML程序转换、位宽规划与FATE分布式执行。

```bash
python scripts/generate_dataset.py --bits 1024 --messages 100 --profile small-integers \
  --output input-small.json
mkdir packed-results
python GeniBatch/comparison/compare_paillier.py --dataset input-small.json --backend cpu \
  --mode packed-encryption --pack-size 6 --repeat 5 --warmup 1 \
  --output packed-results/packed.csv --results-output packed-results/packed.json
```

默认槽位168位、数据24位、符号保留40位；1024位模数最多6个此类槽。
结果同时报告logical_count和ciphertext_count；每组采用该组首个输入r。
打包实验的密文数和随机数数量发生变化，与普通加密的运算量不同。
统一summarize会拒绝packed行，避免生成同算子加速比。此模式仅有CPU演示。

## 五、已完成的验证

已在Windows编译CPU C++适配器并通过27项合约测试；GeniBatch/FATE的12项测试通过；
共享输入与汇总的11项测试通过。64、256、512、1024、2048位样例逐项密文一致。
统一runner在256/512/1024位样例完成GeniBatch/FATE CPU、你的CPU1及CPU4的真实集成运行。
最终统一运行见 validation/integration-cpu-final/；其他验证记录见 validation/ 与 GeniBatch/validation/，这些本地短样例不作为论文性能结论。
CPU工具链为临时便携GCC16.2.0/OpenMP/GMP6.3.0；Python为3.12.14/gmpy2 2.3.0。
最终包不含该Windows可执行文件或工具链，目标Ubuntu环境请从源码重编译。
尚未在目标Ubuntu/CUDA上编译或实跑本包GPU适配器；GPU正确性与性能待目标机验证。

## 六、目录

```text
GeniBatch/upstream              官方源码原样快照，含MIT许可证
GeniBatch/vendor/fate-v1.8.0    官方CPU依赖源码，含Apache2许可证和SOURCE.json
GeniBatch/comparison           对比入口与严格参数检查
ZKP-Paillier-SP24-GPU4060       用户源代码与CGBN原样副本，去除旧构建/实验缓存
zkp_adapter                   新增共同算子C++程序、独立CMake、可选接入安装器和测试
scripts                       构建、共享数据生成、统一运行、汇总脚本
shared                        数据合同与oracle
examples                      可重复使用的公开样例输入
validation                    实际CPU编译/测试/集成日志与验证状态
user_source_manifest.json      用户源码逐文件SHA256以及原ZIP SHA256
package_manifest.json          最终交付文件的SHA256
```

本包不自动发布、不上传代码、不执行提供的论文模板或上游实验文件中的指令。
