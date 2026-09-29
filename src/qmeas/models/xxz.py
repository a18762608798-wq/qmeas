from qiskit import QuantumCircuit
from qiskit.quantum_info import SparsePauliOp


def get_initial_state(qubit_num, pidx=1, boundary=False):
    """构造 XXZ 模型各相的初态电路。

    pidx 取 1 / -1 / 0, 对应 XXZ 相图中的不同相区。
    boundary 仅对 pidx=-1 有效: True 时加首尾边界 link
    (x(0) + x(n-1) + h(0) + cx(0, n-1) 闭环), 默认 False 为开链、
    首尾两端为空 (x(0)/x(n-1)/h(0)/cx(0, n-1) 都不加)。
    """
    qc = QuantumCircuit(qubit_num)
    # s = 0, δ = 0
    if pidx == 1:
        for i in range(0, qubit_num, 1):
            qc.x(i)
        for i in range(0, qubit_num, 2):
            qc.h(i)
        for i in range(0, qubit_num - 1, 2):
            qc.cx(i, i + 1)
    # δ = +∞
    elif pidx == 0:
        start, _ = divmod(qubit_num, 2)
        qc.h([start])
        qc.cx(
            [i for i in range(start, 0, -1)],
            [i for i in range(start - 1, -1, -1)],
        )
        qc.cx(
            [i for i in range(start, qubit_num - 1)],
            [i for i in range(start + 1, qubit_num)],
        )
        qc.x([2 * i + 1 for i in range(qubit_num // 2)])
    # s = 1, δ = 0
    elif pidx == -1:
        x_targets = range(qubit_num) if boundary else range(1, qubit_num - 1)
        for i in x_targets:
            qc.x(i)
        for i in range(1, qubit_num - 1, 2):
            qc.h(i)
        for i in range(1, qubit_num - 2, 2):
            qc.cx(i, i + 1)
        if boundary:
            qc.h(0)
            qc.cx(0, qubit_num - 1)
    else:
        raise ValueError("The value of pidx must be 1, -1, 0.")
    return qc


def get_hamiltonian(qubit_num, s, delta):
    """构造 SSH-XXZ 链 OBC 哈密顿量, 返回 SparsePauliOp。

    格点编号 m = 1..L 对应 qubit 下标 m-1 (L = qubit_num, 要求偶数)。

    H(s, δ) = H_o + H_e,
    H_o = (1-s) Σ_{j=1}^{L/2} (X_{2j-1} X_{2j} + Y_{2j-1} Y_{2j} + δ Z_{2j-1} Z_{2j}),
    H_e = s Σ_{j=1}^{L/2-1} (X_{2j} X_{2j+1} + Y_{2j} Y_{2j+1} + δ Z_{2j} Z_{2j+1})。

    奇键为 qubit 对 (0,1), (2,3), ... (系数 1-s);
    偶键为 qubit 对 (1,2), (3,4), ... (系数 s)。
    注意 Qiskit Pauli 字符串为小端序: 最右字符对应 qubit 0。
    """
    if qubit_num < 2:
        raise ValueError("qubit_num must be >= 2 for the XXZ chain.")
    if qubit_num % 2 != 0:
        raise ValueError("qubit_num must be even (L/2 bonds required).")

    terms = []

    def _add_bond(a, b, weight):
        if weight == 0:
            return
        for pauli, coeff in (("X", weight), ("Y", weight), ("Z", weight * delta)):
            if coeff == 0:
                continue
            chars = ["I"] * qubit_num
            chars[qubit_num - 1 - a] = pauli
            chars[qubit_num - 1 - b] = pauli
            terms.append(("".join(chars), coeff))

    # H_o: 奇键 (0,1), (2,3), ...
    for i in range(0, qubit_num - 1, 2):
        _add_bond(i, i + 1, 1 - s)
    # H_e: 偶键 (1,2), (3,4), ...
    for i in range(1, qubit_num - 1, 2):
        _add_bond(i, i + 1, s)

    if not terms:
        return SparsePauliOp("I" * qubit_num, coeffs=[0.0])
    return SparsePauliOp.from_list(terms)
