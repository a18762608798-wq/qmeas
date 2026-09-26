"""基准电路：线性 cluster 态制备 + 稳定子测量设置 + 读出校验。

逻辑比特 0..n-1 按候选链顺序排列；提交时由 runner 经 target_qubits
映射到芯片比特。测量分组复用 estimator 的 group_qubitwise（8 链下
自然分成奇偶两组），旋转角语义与 estimator/basis.py 的 add_meas 一致
（先 rx 后 ry）。
"""

from dataclasses import dataclass, field

import numpy as np
from qiskit import QuantumCircuit
from qiskit.circuit import Clbit
from qiskit.quantum_info import SparsePauliOp

from ..estimator.basis import PAULI_ROTATIONS, group_qubitwise

__all__ = [
    "CIRCUIT_KEYS",
    "CircuitSet",
    "build_circuits",
    "stabilizer_labels",
]

# 每条链固定 4 个电路
CIRCUIT_KEYS = ("stab_g0", "stab_g1", "allzero", "allone")


def stabilizer_labels(n: int, ring: bool = False) -> list[str]:
    """线性 cluster 稳定子 S_i 的 qiskit 标签（左为高位）。

    S_0 = X_0 Z_1；S_i = Z_{i-1} X_i Z_{i+1}；S_{n-1} = Z_{n-2} X_{n-1}。
    ring=True 时下标模 n（闭环），要求 n 为偶数以保证奇偶两组可分。
    """
    if ring and n % 2:
        raise ValueError(f"ring 只支持偶数长度，当前 {n}")
    labels = []
    for i in range(n):
        chars = ["I"] * n
        chars[n - 1 - i] = "X"
        prev_ = (i - 1) % n if ring else i - 1
        next_ = (i + 1) % n if ring else i + 1
        if ring or i > 0:
            chars[n - 1 - prev_] = "Z"
        if ring or i < n - 1:
            chars[n - 1 - next_] = "Z"
        labels.append("".join(chars))
    return labels


def _cluster_prep(n: int, ring: bool = False) -> QuantumCircuit:
    qc = QuantumCircuit(n, n)
    qc.h(range(n))
    for i in range(n - 1):
        qc.cz(i, i + 1)
    if ring:
        qc.cz(n - 1, 0)
    return qc


def _add_basis_rotations(qc: QuantumCircuit, meas_basis) -> None:
    """按测量基加 rx/ry 旋转（角查 PAULI_ROTATIONS，与 estimator 一致）。"""
    n = len(meas_basis)
    for i in range(n):
        x, z = bool(meas_basis.x[i]), bool(meas_basis.z[i])
        idx = 0 if (x and not z) else (1 if (x and z) else 2)
        qc.rx(float(PAULI_ROTATIONS[idx, 0]), i)
        qc.ry(float(PAULI_ROTATIONS[idx, 1]), i)


@dataclass
class CircuitSet:
    n: int
    circuits: dict[str, QuantumCircuit] = field(default_factory=dict)
    # stab_g0/stab_g1 各自的 Pauli 组（供 QubitwiseBasis.recover 用）
    stab_groups: dict[str, list] = field(default_factory=dict)


def build_circuits(n: int, ring: bool = False) -> CircuitSet:
    """构造 4 个基准电路；稳定子分组走 group_qubitwise（偶数下奇偶两组）。"""
    ops = [SparsePauliOp(label) for label in stabilizer_labels(n, ring)]
    groups, meas_pauli_ls = group_qubitwise(ops)
    if len(groups) != 2:
        raise RuntimeError(f"{n} {'环' if ring else '链'}稳定子应分成 2 组，实际 {len(groups)} 组")

    out = CircuitSet(n=n)
    for g, (group, basis) in enumerate(zip(groups, meas_pauli_ls)):
        qc = _cluster_prep(n, ring)
        _add_basis_rotations(qc, basis)
        qc.measure(range(n), range(n))
        key = f"stab_g{g}"
        out.circuits[key] = qc
        out.stab_groups[key] = list(group)

    q0 = QuantumCircuit(n, n)
    q0.measure(range(n), range(n))
    out.circuits["allzero"] = q0

    q1 = QuantumCircuit(n, n)
    q1.x(range(n))
    q1.measure(range(n), range(n))
    out.circuits["allone"] = q1
    return out
