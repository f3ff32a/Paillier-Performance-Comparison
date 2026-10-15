# Paillier 性能对比项目

本仓库汇集 ZKP-Paillier 串行基线，以及参考文献 7、参考文献 8 的 Paillier 加密算子对比工程。三个项目分别保存在独立文件夹中，各自保留源码、构建入口、使用文档和现有实验记录。

## 项目与使用文档

| 项目目录 | 内容 | 使用文档 |
| --- | --- | --- |
| [ZKP-Paillier-SP24-Origin-Ubuntu26](ZKP-Paillier-SP24-Origin-Ubuntu26/) | 原始串行 CPU 基线与 Ubuntu 兼容资料，保留完整 ZKP-Paillier 流程。 | [README](ZKP-Paillier-SP24-Origin-Ubuntu26/README.md) |
| [Paillier-Ref7-Comparison](Paillier-Ref7-Comparison/) | 参考文献 7（GeniBatch）的普通 Paillier 加密算子对比适配工程。 | [README](Paillier-Ref7-Comparison/README.md) |
| [Ref8-Paillier-Comparison](Ref8-Paillier-Comparison/) | 参考文献 8 的普通 Paillier 加密算子对比适配工程。 | [README](Ref8-Paillier-Comparison/README.md) |

## 运行方式

进入对应项目目录，按照该项目的 README 安装依赖、构建和运行。三个项目的构建入口保持独立，具体命令、参数和输出格式见各自文档。

## 比较范围与实验记录

串行基线保留完整 ZKP-Paillier 协议流程；参考文献 7、8 的对比工程采用共享输入比较普通 Paillier 加密算子。完整协议耗时与单个加密算子耗时应分别分析。

性能比较需要匹配计算内容、输入、实际位宽、后端、硬件和计时范围。CPU 功能验证、CUDA 编译、GPU 实机正确性和性能测量的完成状态，分别见各项目 README 及验证记录。

仓库保留原有 CSV、LOG、JSON、固定输入和验证资料，便于核对实验参数、结果和来源。

## 来源与许可

各项目的来源记录、固定版本信息和第三方许可证保存在原目录中，相关代码遵循各自的许可声明。
