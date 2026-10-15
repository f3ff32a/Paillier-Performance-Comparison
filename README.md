# Paillier Performance Comparison

本仓库将三个性能对比相关项目保存在三个独立文件夹中，各项目保留自己的源码、构建入口、使用说明及现有结果记录。

| 文件夹 | 内容与入口 |
| --- | --- |
| [ZKP-Paillier-SP24-Origin-Ubuntu26](ZKP-Paillier-SP24-Origin-Ubuntu26/) | 原始串行 CPU 基线与 Ubuntu 兼容资料；参见目录内 README.md。 |
| [Paillier-Ref7-Comparison](Paillier-Ref7-Comparison/) | 参考文献 7 的 Paillier 对比适配项目；参见目录内使用说明.txt。 |
| [Ref8-Paillier-Comparison](Ref8-Paillier-Comparison/) | 参考文献 8 的 Paillier 对比适配项目；参见目录内使用说明.txt。 |

## 使用

进入对应文件夹，按该目录的原始说明安装依赖、构建和运行。三个文件夹作为普通 Git 目录保存，各自的构建入口保持独立。

## 实验与验证记录

原有 CSV、LOG、JSON、固定输入和验证资料随项目保存。现有记录的硬件、参数、计时范围及验证状态，以各项目说明和记录为准。

完整 ZKP 流程与普通 Paillier 加密算子采用各自的计时范围。性能比较需要匹配计算内容、输入、实际位宽、后端和硬件；CPU 功能验证、CUDA 编译、GPU 实机正确性及性能测量应分别报告。

## 来源与许可

各项目的来源记录、固定版本信息和第三方许可证保存在原目录中，相关代码遵循各自的许可声明。
