# ZKP-Paillier-SP24-GPU2 2.0.0

基线是这次上传的 CPU v6（与此前附件内容相同），保留原 CPU 路径并继承已回归的 CUDA/CGBN 实现。

- RTX 4060 / RTX 4060 Laptop 默认架构改为 `89-real`，生成原生 sm_89 机器码。
- 统一 C++ 与 CUDA 主机编译器；配置时增加 NTL/GMP 编译、链接和运行探针，提前定位 CXXABI / libstdc++ 冲突。
- 增加 `build.py`，默认使用系统 g++、独立构建目录和 CUDA 13.4；旧缓存可改名保留后重建。
- 增加真实 CUDA 内核诊断程序、环境报告，以及严格的 GPU 正确性/完整证明验收脚本。
- CUDA Runtime 使用静态链接，减少未设置 `LD_LIBRARY_PATH` 时找不到 libcudart 的问题；NVIDIA 驱动仍由系统提供。
- GPU 预检查增加内核架构加载检查；CUDA 异常保留错误名称和编号。
- GPU 测试、验收、性能脚本统一支持设备编号选择；性能脚本支持显存预算设置。
- 保留 CPU/OpenMP、CUDA、混合、自动模式和动态任务队列；GPU 缺失的回退结果不能作为 GPU 验收或性能成绩。
- 补充 Ubuntu 26.04 / RTX 4060 的中文安装、运行和排错指南，更新验证记录。
- 项目名称、版本及发布目录统一为 `ZKP-Paillier-SP24-GPU2`。

# GPU 并行化基础（继承自第一版 GPU 实现）

基线为用户上传的 `ZKP-Paillier-SP24-parallel-v6(4).zip`。

- 增加 NVIDIA CUDA/CGBN 大整数后端；Commit 按行和 8 位窗口拆分，在 GPU 内合并窗口结果。
- 普通 Paillier 密文增加 GPU 模幂和最终模乘，保留 CPU/GMP 加密路径。
- 增加 CPU、CUDA、混合、自动四种模式；CPU/GPU 使用同一原子任务队列，GPU 批量依据实际耗时调整。
- 增加显式 GPU 缓冲预算、实际模数位宽选择、错误回退、可选逐项结果检查与分项计时。
- 修改 `CBatchEnc::encrypt` 和矩阵 `PolynomialCommitment::commit` 接入调度；保持辅助密文、电路、随机数和验证逻辑。
- 修复原单向量 Commit 对空输入访问 `zmi[0]` 的边界问题，增加矩阵/随机数/生成元维度检查。
- 新增 CMake 构建、命令行 benchmark、正确性测试、原 v6 证明记录夹具和性能汇总脚本。
- 项目与发布目录更名为 `ZKP-Paillier-SP24-GPU2`，原 CPU 说明归档至 `docs/legacy`。

本版本的 GPU 设备执行和性能数据待实际 GPU 验证，详见 `docs/VALIDATION.md`。
