# 串行兼容改动与验证范围

此版本的输入是用户上传的 `ZKP-Paillier-SP24-master(4).zip`，不是 OpenMP/v6/GPU 代码的 CPU 模式。原作者串行算法保留，无 OpenMP/CUDA 内核；默认优化为 `-O3`，去掉原构建中默认的覆盖率插桩。基准入口调用 NTL::SetNumThreads(1)，并在日志与结果行记录实际 AvailableThreads()=1。

## 必要修复

| 文件 | 原问题 | 改动及影响 |
|---|---|---|
| Makefile | 依赖未随 ZIP 提供的 gtest、自动跑大参数与覆盖率、没有可直接调参的程序 | 新的本机串行构建入口，独立 check/smoke，不默认跑大实验 |
| CBatchEnc.cpp | RandomBits_ZZ 转换会省略高位零字节，后续按固定长度下标读取 | 固定长度补零并拒绝错误长度；相同随机整数不变 |
| PolynomialCommitment.cpp | 空向量读 mi[0]，缺少生成元/随机数数量校验 | 空积定义为 g^r，增加输入边界检查；正常桶式 Commit 计算保留 |
| math/Matrix.cpp | group(0) 除零；稀疏键比较误用 0..size-1 | 参数拒绝及按实际稀疏键比较；正常电路计算不变 |
| EndToEnd.cpp/.hpp | 解密后二进制串可能不足固定宽度；附属演示 ASCII、位序、整数除法错误；验证失败仍写 CSV | 补零并修复附属平均值演示，检查聚合解密；验证失败在写行前抛错；ostream 输出可选择不结束旧行以追加元数据 |
| tools/serial_benchmark.cpp | 只能改测试源码调参数，无单线程执行声明或可靠重复输出 | 新 CLI、参数校验、重复运行、新文件保护、独立 keygen/wall 与验证状态 |
| scripts 与 tests | 缺少可核查批量运行/续跑/环境/失败状态 | 每配置独立子进程，按请求分组，严格 PASS/次数核验、环境指纹及汇总；自包含测试不依赖 gtest |

没有将 v6 的 generateProverAndRelease 等生命周期优化移植为“原作者串行基线”。没有改变证明关系、挑战计算公式或承诺公式；补零修复只补随机整数的零高位。

## 计时口径

1. 旧 35 列保留名称、顺序及阶段定义；原 Timer 文件保留。阶段以毫秒截断后换成秒；附属 phase3 另记纳秒。
2. `wall_seconds` 从进入一次 end_to_end 开始，到函数正常返回、局部对象析构后结束。旧 CSV 前 35 列的写出/flush 在调用内，与旧 GPU 入口一致；新增 7 列与 PASS 行在捕获墙钟后输出。密钥生成在外部，单独记 `keygen_seconds`。
3. 每个 MC 配置生成一个 key，三次重复复用。和现有 GPU 批量入口一致；与原作者旧 App_test 的跨多个 MC 复用 key 有差异，应在方法中说明。
4. 附属 phase3 纠错不参与 Commit/Prove/Verify。它的测量不可与旧错误实现作为同算法性能比较；整个墙钟中包含这项纠错以及新增聚合一致性检查的微小开销。
5. 串行与现有 v6/GPU 若同时有其他优化，对比结果代表总实现变化。纯并行贡献需额外在同一算法/生命周期基础上做消融对照。

## 已完成验证

- 原作者核心源码与兼容核心源码：同一固定输入的完整证明记录逐字节一致。记录包含密文、辅助/范围密文、Kq、生成元、ABC/D 承诺、多项式承诺、挑战 x/y、proofs；测试参数为 128 bit、MC=3、range=8、slot=4、batch=15。
- 两份记录 SHA-256：`d625c72686034c4dcf78087bc756c87bc641e0bf43f3a87e85189e9394552b5a`。
- 完整证明接受、篡改拒绝、解密、承诺与定义一致、NTL 模数恢复、对象销毁、零高位挑战和非法长度回归通过。
- CLI 的 MC=1/零聚合、三次/多次重复、旧 CSV 前缀、单线程声明、新文件保护及非法并行参数拒绝通过。
- 批量脚本用受控测试程序检查成功、失败、超时、中断、参数/结果损坏后的续跑、A/B 分组和 2048 bit 单独输出；没有用模拟性能数据填入正式结果。

具体编译器与补充端到端结果记录在随包的 `reference/VALIDATION.txt`。当前验证在远程 Linux 环境进行，并非用户电脑上的 Ubuntu 26.04 真机验收；实际 Ubuntu 26.04 依赖应使用其同版本官方包，在目标机器本机编译和运行 `make check`。

## 可复核

- `reference/author-source.zip`：未修改的输入归档。
- `reference/compatibility.patch`：原作者文本文件到本版本的补丁。
- `reference/source-manifest.json`：原始和修改后的文件 SHA-256。
- `tests/serial_core_check.cpp --snapshot`：可用于输出固定输入完整记录。需要编译测试程序后执行 `./build/serial_core_check --snapshot`。

以上是兼容性与功能回归测试，不是协议安全性证明；未在六台用户机器上重新测量性能，也未承诺机器能完成任意大参数。
