import numpy as np
from qiskit.circuit import Clbit, ParameterVector
from qiskit.quantum_info import Pauli, PauliList

# 测量基旋转角: 每行 [rx 角(绕 x), ry 角(绕 y)], 对应待测 Pauli。
PAULI_ROTATIONS = np.array(
    [
        [0, -np.pi / 2],  # X
        [np.pi / 2, 0],  # Y
        [0, 0],  # Z
    ],
    dtype=float,
)


class QubitwiseBasis:
    """逐比特对易测量基：从直方图恢复组内每个 Pauli 期望。

    测量基信息已在 rotation gate 中编码，本类只负责对 Z-basis 计数做
    奇偶校验，还原对应的 Pauli 期望值。

    后续可扩展为 PairBasis / GeneralBasis，对应纠缠对易组的恢复逻辑。
    """

    def recover(self, group, counts, shots):
        masks = self._build_masks(group)

        sums = {pauli: 0 for pauli in group}
        for bitstring, count in counts.items():
            outcome = int(bitstring, 2)
            for pauli in group:
                parity = (masks[pauli] & outcome).bit_count() & 1
                sums[pauli] += count * (-1 if parity else 1)

        return {pauli: s / shots for pauli, s in sums.items()}

    @staticmethod
    def _build_masks(pauli_list):
        masks = {}
        for pauli in pauli_list:
            support = pauli.x | pauli.z
            mask = 0
            for i, bit in enumerate(support):
                if bit:
                    mask |= 1 << i
            masks[pauli] = mask
        return masks


def group_qubitwise(observables):
    """把所有待测 observable 的 Pauli 项去重后按「逐比特对易」分组, 并为每组算出统一的测量基.

    Returns:
        groups: 对易分组.
        meas_pauli_ls: 每一组的测量基.
    """
    pauli_set = {label for ob in observables for label in ob.paulis.to_labels()}
    pauli_list = PauliList(sorted(pauli_set))
    groups = pauli_list.group_commuting(qubit_wise=True)
    meas_pauli_ls = []
    for group in groups:
        basis_x = np.logical_or.reduce(group.x, axis=0)
        basis_z = np.logical_or.reduce(group.z, axis=0)
        meas_pauli_ls.append(Pauli((basis_z, basis_x)))
    return groups, meas_pauli_ls


def add_meas(qc, num_qubits):
    # 允许输入电路不带经典比特: 不足 num_qubits 时自动补齐, 供下方测量使用.
    if (missing := num_qubits - qc.num_clbits) > 0:
        qc.add_bits([Clbit() for _ in range(missing)])
    theta_x = ParameterVector("θx", num_qubits)  # rx 角: 绕 x 轴
    theta_y = ParameterVector("θy", num_qubits)  # ry 角: 绕 y 轴
    for i in range(num_qubits):
        qc.rx(theta_x[i], i)
        qc.ry(theta_y[i], i)
    qc.measure(range(num_qubits), range(num_qubits))
    return qc, theta_x, theta_y


def bind_group_rotation(qc, group_idx, meas_pauli_ls, theta_x, theta_y):
    binds = _group_params(group_idx, meas_pauli_ls, theta_x, theta_y)
    return qc.assign_parameters(binds)


def _group_params(group_idx, meas_pauli_ls, theta_x, theta_y):
    num_qubits = len(meas_pauli_ls[0])
    pauli = meas_pauli_ls[group_idx]
    binds = {}
    for i in range(num_qubits):
        if pauli.x[i] and not pauli.z[i]:
            idx = 0  # X
        elif pauli.z[i] and not pauli.x[i]:
            idx = 2  # Z
        elif pauli.x[i] and pauli.z[i]:
            idx = 1  # Y
        else:
            idx = 2  # I → behave as Z (no rotation needed)
        binds[theta_x[i]] = PAULI_ROTATIONS[idx, 0]
        binds[theta_y[i]] = PAULI_ROTATIONS[idx, 1]
    return binds


def rebuild_op_vals(observables, expval_map):
    """把分量期望拼回完整可观测量的期望.

    expval_map：一个 `{Pauli: 期望值}` dict.
    """
    vals = []
    for ob in observables:
        v = 0.0
        for pauli, coef in zip(ob.paulis, ob.coeffs):
            v += coef * expval_map[pauli]
        vals.append(v)
    return vals
