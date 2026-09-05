from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from qiskit import QuantumCircuit
from qiskit.circuit import ParameterVector

Ensemble = Literal["haar", "pauli"]


@dataclass(frozen=True)
class SettingRun:
    num_settings: int
    num_shots: int


@dataclass(frozen=True)
class ConjugatePair:
    """时间反演配对实验（测 Z_T 用）。

    开启后一次 run_random 产生两份设置逐行配对的数据：
    实验一先对 I_1 区施加 u_T（逐比特 σ^y），再做普通 Haar 采样，
    即 U^{(1)} = U_{I_1} u_T ⊗ U_{I_2}；
    实验二的 I_1 区幺正取 Haar 部分的复共轭，I_2 区与实验一相同，
    即 U^{(2)} = U_{I_1}^* ⊗ U_{I_2}。
    注意 u_T 不能吸收进 Haar 采样：它旋转的是被测态本身，
    估计子依赖旋转后的态。

    布局约定（Julia 侧要求）：meas_indices 须按 I_1、I_2 交错排列，
    即 [(i1_1,), (i2_1,), (i1_2,), (i2_2,), ...]，此时 i1_groups=(0, 2, ...)；
    Julia 侧重排 frame 下奇位即 I_1、偶位即 I_2。
    """

    i1_groups: tuple[int, ...] = ()


@dataclass(frozen=True)
class AerOptions:
    method: str = "matrix_product_state"
    device: str = "CPU"
    # shadow 估计子里有 3^n 量级的系数放大，单精度舍入噪声会被同步放大，
    # 故默认双精度。
    precision: str = "double"
    mitigation: bool = False


@dataclass
class QuarkOptions:
    chip: str = "Baihua"
    token: str | None = None
    target_qubits: list[int] = field(default_factory=list)
    mitigation: bool = False
    coupling_map: list | None = None
    optimization_level: int = 3
    basis_gates: list[str] = field(default_factory=lambda: ["rz", "rx", "ry", "cz"])
    correct: bool = False


@dataclass
class RandomMeasConfig:
    qc: QuantumCircuit
    setting_runs: list[SettingRun]
    meas_indices: list[tuple[int, ...]]
    runner_opts: AerOptions | QuarkOptions = field(default_factory=AerOptions)
    ensemble: Ensemble = "haar"
    conjugate_pair: ConjugatePair | None = None
    seed: int | None = None
    output_dir: Path = field(default_factory=lambda: Path("./data"))
    name: str = "experiment"

    def __post_init__(self) -> None:
        self.output_dir = Path(self.output_dir)

        # 初始化 ParameterVector
        group_num = len(self.meas_indices)
        self.params = [
            ParameterVector("theta", group_num),
            ParameterVector("phi", group_num),
        ]

        # 检查输入格式
        # 输入只接受纯态制备电路: 带经典位即报错
        if self.qc.num_clbits:
            raise ValueError("qc 不能带经典比特, 请只给纯态制备电路 QuantumCircuit(n)")

        if not self.setting_runs:
            raise ValueError("setting_runs cannot be empty")

        if not self.meas_indices:
            raise ValueError("meas_indices cannot be empty")

        if any(len(group) == 0 for group in self.meas_indices):
            raise ValueError("meas_indices groups cannot be empty")

        n = self.qc.num_qubits
        for group in self.meas_indices:
            for idx in group:
                if not isinstance(idx, int) or not 0 <= idx < n:
                    raise ValueError(
                        f"meas_indices index {idx!r} out of range for {n}-qubit circuit"
                    )

        # 共轭配对（Z_T）的前提检查：twirl 恒等式要求 Haar 系综与各 site 独立幺正，
        # 归一化分母要求 I_1、I_2 分区都非空
        if self.conjugate_pair is not None:
            # hamming 互关联的两个 twirl 在 haar 与 pauli 三基下都精确成立，
            # pauli 只是 sem 更大（每比特仅 3 种幺正，twirl 平均不充分）
            if self.ensemble not in ("haar", "pauli"):
                raise ValueError("conjugate_pair 要求 ensemble='haar' 或 'pauli'")
            if any(len(group) != 1 for group in self.meas_indices):
                raise ValueError("conjugate_pair 要求每个 group 恰含 1 个比特")
            i1 = self.conjugate_pair.i1_groups
            if not i1 or len(i1) == len(self.meas_indices):
                raise ValueError("conjugate_pair 的 i1_groups 必须非空且不等于全体")
            if any(
                not isinstance(g, int) or not 0 <= g < len(self.meas_indices)
                for g in i1
            ):
                raise ValueError(
                    f"conjugate_pair 的 i1_groups 下标越界: {i1!r}"
                )
