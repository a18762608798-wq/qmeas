# qmeas

量子测量工具箱。

## 模块

- `qmeas.random` — 随机测量（经典影子），在 Qiskit Aer（本地）或 quarkstudio（远程云）上运行。
- `qmeas.estimator` — 旋转测量基估计器。Aer 路径直接调官方 `EstimatorV2`；Quark 路径做逐比特对易分组，加旋转门后分别提交任务，从直方图恢复 Pauli 期望值。

## 安装

见 [INSTALL.md](INSTALL.md)。

## 快速开始

### random

见 [example/random/](example/random/)：

- Aer（本地仿真）：[aer_example.py](example/random/aer_example.py)
- Quark（云端）：[quark_example.py](example/random/quark_example.py)

### estimator

（待补充）

### models

（待补充）

## License

Apache-2.0，见 [LICENSE](LICENSE)。

## 致谢 / 上游依赖协议

本包仅通过 `pip` / `CondaPkg` 引用以下第三方库（未复制其源码），各库版权归各自作者所有：

- Qiskit（`qiskit`）— Apache-2.0，IBM 及其贡献者。
- Qiskit Aer（`qiskit-aer`）— Apache-2.0，IBM 及其贡献者。
- quarkstudio — MIT（见 PyPI 标注），北京量子信息科学研究院超导量子计算团队。
- NumPy（`numpy`）— BSD-3-Clause。
