"""Quark 云端随机测量示例：全部参数显式写出（含默认值）。

运行（项目根目录）: export QUARK_TOKEN=... && python example/random/quark_example.py
真实提交云端任务并轮询，会消耗机时；本地调试见 aer_example.py。
"""

import asyncio
import os
from pathlib import Path

from qmeas.models.xxz import get_initial_state
from qmeas.random import (
    ConjugatePair,
    QuarkOptions,
    RandomMeasConfig,
    SettingRun,
    run_random,
)

# 待测态：纯态制备电路，不能带经典比特（与 aer_example.py 相同）。
qc = get_initial_state(8, pidx=-1, boundary=False)

runner_opts = QuarkOptions(
    chip="Dongling",  # 默认 "Baihua"；随任务提交
    token=os.environ.get("QUARK_TOKEN"),  # 默认 None，此时读环境变量 QUARK_TOKEN；勿把 token 写进代码提交
    target_qubits=[],  # 默认 []（芯片端自动布局）；非空如 [2, 3, 4, 5]，透传给云端
    mitigation=False,  # 默认值；True 则每 setting 多提交一份标定任务（后缀 _calib_U<i>）
    coupling_map=None,  # 默认 None；本地 transpile 约束，一般保持 None 交给云端 compiler
    optimization_level=3,  # 默认值；0~3（裸测量比特由 runner 自动补 rz(0)，无需担心）
    basis_gates=["rz", "rx", "ry", "cz"],  # 默认值；硬件原生门集，提交前转 QASM2
    correct=False,  # 默认值；透传云端 task["options"]["correct"]
)

config = RandomMeasConfig(
    qc=qc,  # 必填，见上
    # 必填。云端任务数 = sum(num_settings)（mitigation=True 时 ×2）；先用小参数确认链路。
    setting_runs=[
        SettingRun(num_settings=2, num_shots=1024),
    ],
    # 必填；同组共享同一随机幺正。只测中间 4 比特，镜面对称配对。
    meas_indices=[(2, 5), (3, 4)],
    runner_opts=runner_opts,  # 默认 AerOptions()；此处换 QuarkOptions 即走云端
    ensemble="haar",  # 默认值；或 "pauli"
    # 默认 None（关闭）。开启要求单比特分组且 I_1/I_2 交错；当前每组 2 比特，开启须先改分组。
    conjugate_pair=None,
    seed=42,  # 默认 None；填 int 可复现
    output_dir=Path(__file__).resolve().parent / "data_quark",  # 默认 "./data"；自动建目录
    name="quark_demo",  # 默认 "experiment"；同时是云端任务名前缀（<name>_setting<k>_U<i>）
)


def main() -> None:
    if runner_opts.token is None:
        raise RuntimeError("未找到 QUARK_TOKEN：请先 export QUARK_TOKEN=... 再运行")
    summary = asyncio.run(run_random(config))
    print(summary)

    import numpy as np

    for npz_name in summary["npz_files"]:
        data = np.load(config.output_dir / npz_name)
        print(
            npz_name,
            "results:", data["measurement_results"].shape,
            "settings:", data["measurement_settings"].shape,
            "trivial:", "trivial_measurement_results" in data,
        )


if __name__ == "__main__":
    main()
