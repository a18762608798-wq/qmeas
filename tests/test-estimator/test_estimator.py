import asyncio

from qiskit import QuantumCircuit
from qiskit.quantum_info import SparsePauliOp

from qmeas.estimator import (
    AerEstimatorOptions,
    EstimatorConfig,
    QuarkEstimatorOptions,
    group_qubitwise,
    run_estimator,
)


def _example_qc() -> QuantumCircuit:
    qc = QuantumCircuit(3)
    qc.h(0)
    qc.x(1)
    qc.h(1)
    qc.x(2)
    qc.h(2)
    return qc


def _observables():
    return [
        SparsePauliOp(["IIZ"], coeffs=[1.0]),
        SparsePauliOp(["IIX"], coeffs=[1.0]),
        SparsePauliOp(["IXI"], coeffs=[1.0]),
        SparsePauliOp(["XII"], coeffs=[1.0]),
    ]


if __name__ == "__main__":
    # group_qubitwise check
    groups, bases = group_qubitwise(_observables())
    print("groups:", [g.to_labels() for g in groups])
    print("bases:", [str(b) for b in bases])

    # Aer EstimatorV2 check
    qc = _example_qc()

    aer_config = EstimatorConfig(
        qc=qc,
        observables=_observables(),
        runner_opts=AerEstimatorOptions(),
    )
    result = asyncio.run(run_estimator(aer_config))
    print("aer evs:", result["evs"])

    # quafu check
    Quafu_config = EstimatorConfig(
        qc=qc,
        observables=_observables(),
        runner_opts=QuarkEstimatorOptions(
            chip="Dongling",
            shots=1024,
            # target_qubits=[138, 125],
            name="Dongling-estimator-check",
        ),
    )
    result = asyncio.run(run_estimator(Quafu_config))
    print("Baihua evs:", result["evs"])
