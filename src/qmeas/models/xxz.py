from qiskit import QuantumCircuit


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
        x_targets = range(0, qubit_num) if boundary else range(1, qubit_num - 1)
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
