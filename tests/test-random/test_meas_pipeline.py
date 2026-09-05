import asyncio
import os
from pathlib import Path


from qiskit import QuantumCircuit


from qmeas.random import (
    AerOptions,
    QuarkOptions,
    RandomMeasConfig,
    SettingRun,
    run_random,
)


test_idx = 2

if __name__ == "__main__":
    HERE = Path(__file__).resolve().parent
    if test_idx == 1:
        # aer: independence, pauli
        qc = QuantumCircuit(4)
        meas_indices = [(0,), (1,), (2,), (3,)]  # Arrange the swap bits together
        setting_runs = [
            SettingRun(num_settings=3**3, num_shots=1024),
            SettingRun(num_settings=3**4, num_shots=1024),
        ]
        aer_opts = AerOptions(
            method="statevector", device="CPU", precision="single", mitigation=True
        )
        meas_config = RandomMeasConfig(
            qc=qc,
            setting_runs=setting_runs,
            meas_indices=meas_indices,
            ensemble="pauli",
            runner_opts=aer_opts,
            output_dir=HERE / "data/aer-mitigation-independence-pauli/",
            name="aer-mitigation-independence-pauli",
        )
        res = asyncio.run(
            run_random(
                config=meas_config,
            )
        )
        print(res)
    elif test_idx == 2:
        # aer: pair, haar
        qc = QuantumCircuit(6)
        meas_indices = [(2, 5), (3, 4)]  # Groups sharing rotation params
        setting_runs = [
            SettingRun(num_settings=2, num_shots=1024),
            SettingRun(num_settings=5, num_shots=1024),
        ]
        aer_opts = AerOptions(
            method="matrix_product_state", device="CPU", precision="single"
        )
        meas_config = RandomMeasConfig(
            qc=qc,
            setting_runs=setting_runs,
            meas_indices=meas_indices,
            ensemble="haar",
            runner_opts=aer_opts,
            output_dir=HERE / "data/aer-shared-haar",
            name="aer-shared-haar",
        )
        res = asyncio.run(
            run_random(
                config=meas_config,
            )
        )
        print(res)
    elif test_idx == 3:
        # quark-native-independence, pauli
        qc = QuantumCircuit(6)
        qc.cz(list(range(5)), list(range(1, 6)))
        meas_indices = [(2,), (3,), (4,), (5,)]
        setting_runs = [
            SettingRun(num_settings=2, num_shots=1024),
            SettingRun(num_settings=5, num_shots=2048),
        ]

        quark_opts = QuarkOptions(
            chip="Dongling",
            target_qubits=[],
            token=os.environ["QUARK_TOKEN"],
        )
        meas_config = RandomMeasConfig(
            qc=qc,
            setting_runs=setting_runs,
            meas_indices=meas_indices,
            ensemble="pauli",
            runner_opts=quark_opts,
            output_dir=HERE / "data/quark-independence-pauli",
            name="quark-independence-pauli",
        )
        res = asyncio.run(run_random(config=meas_config))
        print(res)
    elif test_idx == 4:
        # quark-mitigation-pair, haar
        qc = QuantumCircuit(6)
        qc.cx(list(range(5)), list(range(1, 6)))
        meas_indices = [(2, 5), (3, 4)]
        setting_runs = [
            SettingRun(num_settings=2, num_shots=1024),
            SettingRun(num_settings=5, num_shots=2048),
        ]

        quark_opts = QuarkOptions(
            chip="Dongling",
            target_qubits=[],
            token=os.environ["QUARK_TOKEN"],
            mitigation=True,
        )
        meas_config = RandomMeasConfig(
            qc=qc,
            setting_runs=setting_runs,
            meas_indices=meas_indices,
            ensemble="haar",
            runner_opts=quark_opts,
            output_dir=HERE / "data/quark-mitigation-shared-haar",
            name="quark-mitigation-shared-haar",
        )
        res = asyncio.run(run_random(config=meas_config))
        print(res)
