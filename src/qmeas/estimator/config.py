from dataclasses import dataclass, field

from qiskit import QuantumCircuit
from qiskit.quantum_info import SparsePauliOp


@dataclass
class AerEstimatorOptions:
    method: str = "matrix_product_state"


@dataclass
class QuarkEstimatorOptions:
    token: str | None = None
    chip: str = "Baihua"
    shots: int = 1024
    name: str = "estimator"
    compiler: str = "qiskit"
    correct: bool = False
    target_qubits: list[int] = field(default_factory=list)
    coupling_map: list | None = None
    optimization_level: int = 3
    basis_gates: list[str] = field(default_factory=lambda: ["rz", "rx", "ry", "cz"])


@dataclass
class EstimatorConfig:
    qc: QuantumCircuit
    observables: list[SparsePauliOp]
    runner_opts: AerEstimatorOptions | QuarkEstimatorOptions = field(
        default_factory=AerEstimatorOptions
    )

    def __post_init__(self) -> None:
        # 输入只接受纯态制备电路: 带经典位即报错
        # (quark 路径所需经典位由 basis.add_meas 按 num_qubits 自动补齐)
        if self.qc.num_clbits:
            raise ValueError(
                "qc 不能带经典比特, 请只给纯态制备电路 QuantumCircuit(n)"
            )
