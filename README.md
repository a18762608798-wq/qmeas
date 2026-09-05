# qmeas

量子测量工具箱。

## 模块

- `qmeas.random` — 随机测量（经典影子），在 Qiskit Aer（本地）或 quarkstudio（远程云）上运行。
- `qmeas.estimator` — 旋转测量基估计器。Aer 路径直接调官方 `EstimatorV2`；Quark 路径做逐比特对易分组，加旋转门后分别提交任务，从直方图恢复 Pauli 期望值。

## 安装

仓库：`a18762608798-wq/qmeas`。

### Python venv

修改 `python3` 成具体 python 解释器路径.

```bash
python3 -m venv .venv
source .venv/bin/activate
```

```bash
python3 -m pip install --upgrade --force-reinstall "git+https://github.com/a18762608798-wq/qmeas.git@master"
```

本地克隆后可编辑安装：

```bash
python3 -m pip install -e .
```

更新：

```bash
pip install --upgrade "git+https://github.com/a18762608798-wq/qmeas.git@master"
```

### Julia（通过 CondaPkg.jl）

```julia
using CondaPkg

CondaPkg.add("pip")

url = "git+https://github.com/a18762608798-wq/qmeas.git@master"

CondaPkg.withenv() do
    python3 = CondaPkg.which("python3")
    run(`$python3 -m pip install --upgrade $url`)
end
```

本地可编辑安装(**会自动添加toml配置**)：

```julia
using CondaPkg

CondaPkg.rm_pip("qmeas")
path = expanduser("~/qmeas")

CondaPkg.add_pip(
    "qmeas";
    version="@file://$path",
    editable=true,
)
```

更新：

```julia
using CondaPkg

url = "git+https://github.com/a18762608798-wq/qmeas.git@master"

CondaPkg.withenv() do
    python3 = CondaPkg.which("python3")
    run(`$python3 -m pip install --upgrade --force-reinstall --no-cache-dir $url`)
end
```

## 快速开始

```python
import asyncio
from qiskit import QuantumCircuit
from qiskit.quantum_info import SparsePauliOp

from qmeas.estimator import (
    AerEstimatorOptions,
    EstimatorConfig,
    QuarkEstimatorOptions,
    group_qubitwise,
    QubitwiseBasis,
    run_estimator,
)

# 逐比特分组
observables = [
    SparsePauliOp(["XX", "XI", "IX"], coeffs=[1.0, 1.0, 1.0]),
    SparsePauliOp(["ZZ"], coeffs=[1.0]),
]
groups, bases = group_qubitwise(observables)
print("groups:", [g.to_labels() for g in groups])
print("bases:", [str(b) for b in bases])

# 从直方图恢复期望
basis = QubitwiseBasis()
expects = basis.recover(groups[0], {"00": 1024}, shots=1024)

# Aer 估计器
qc = QuantumCircuit(2)
qc.h([0, 1])
cfg = EstimatorConfig(qc=qc, observables=observables, runner_opts=AerEstimatorOptions())
result = asyncio.run(run_estimator(cfg))

# Quark 估计器
cfg = EstimatorConfig(
    qc=qc,
    observables=observables,
    runner_opts=QuarkEstimatorOptions(quark_options={
        "chip": "Dongling",
        "shots": 1024,
        "name": "my-job",
    }),
)
result = asyncio.run(run_estimator(cfg))
```

## 工程结构

```
qmeas/
├── src/qmeas/
│   ├── estimator/
│   │   ├── basis.py       # 逐比特测量流水线: group_qubitwise, QubitwiseBasis, rebuild_op_vals（可扩展 PairBasis / GeneralBasis）
│   │   ├── config.py      # EstimatorConfig, AerEstimatorOptions, QuarkEstimatorOptions
│   │   └── runner.py      # run_estimator
│   └── random/
│       ├── config.py      # RandomMeasConfig, AerOptions, QuarkOptions, ...
│       ├── ensemble.py    # ParameterGenerator, create_parameter_generator
│       └── runner.py      # run_random, add_meas
├── tests/
│   ├── test-estimator/
│   │   ├── data/
│   │   └── test_estimator.py
│   └── test-random/
│       ├── data/
│       ├── test_meas_config.py
│       ├── test_meas_pipeline.py
│       └── test_meas_runner.py
└── pyproject.toml
```

## License

Apache-2.0，见 `LICENSE`。

## 致谢 / 上游依赖协议

本包仅通过 `pip` / `CondaPkg` 引用以下第三方库（未复制其源码），各库版权归各自作者所有：

- Qiskit（`qiskit`）— Apache-2.0，IBM 及其贡献者。
- Qiskit Aer（`qiskit-aer`）— Apache-2.0，IBM 及其贡献者。
- quarkstudio — MIT（见 PyPI 标注），北京量子信息科学研究院超导量子计算团队。
- NumPy（`numpy`）— BSD-3-Clause。

