"""Aer 后端随机测量示例：全部参数显式写出（含默认值）。

运行（项目根目录）: python example/random/aer_example.py
本地仿真，不需要 token。云端版见 quark_example.py。
"""

import asyncio
from pathlib import Path

from qmeas.models.xxz import get_initial_state
from qmeas.random import (
    AerOptions,
    ConjugatePair,
    RandomMeasConfig,
    SettingRun,
    run_random,
)

# 待测态：纯态制备电路，不能带经典比特。
# pidx ∈ {1, 0, -1} 选相区；boundary 仅 pidx=-1 时有效（True 加首尾 link 成闭环）。
qc = get_initial_state(8, pidx=-1, boundary=False)

runner_opts = AerOptions(
    method="matrix_product_state",  # 默认值；小体系可用 "statevector" / "density_matrix"
    device="CPU",  # 默认值；有 GPU 版 Aer 可填 "GPU"
    precision="double",  # 默认值；shadow 系数放大 ~3^n，别用 "single"
    mitigation=False,  # 默认值；True 则每 setting 多跑一份 |0> 标定电路，结果进 npz 的 trivial_* 字段
)

config = RandomMeasConfig(
    qc=qc,  # 必填，见上
    # 必填，至少 1 个。每项 = 采样 N 个随机基 × 每基测 M 次，各 run 独立采样、独立落盘。
    setting_runs=[
        SettingRun(num_settings=10, num_shots=1024),
        SettingRun(num_settings=20, num_shots=2048),
    ],
    # 必填；同组比特共享同一随机幺正。只测中间 4 比特，镜面对称配对；下标须在 [0, 8) 内。
    meas_indices=[(2, 5), (3, 4)],
    runner_opts=runner_opts,  # 默认 AerOptions()；云端版换 QuarkOptions，见 quark_example.py
    ensemble="haar",  # 默认值；或 "pauli"（X/Y/Z 三基）
    # 默认 None（关闭）。开启如 ConjugatePair(i1_groups=(0, 2))，要求单比特分组且 I_1/I_2 交错；
    # 当前每组 2 比特，开启须先改回单比特分组。
    conjugate_pair=None,
    seed=42,  # 默认 None；填 int 可复现
    output_dir=Path(__file__).resolve().parent / "data_aer",  # 默认 "./data"；自动建目录
    name="aer_demo",  # 默认 "experiment"；决定 npz/json 前缀（配对实验再加 _exp1/_exp2）
)


def main() -> None:
    summary = asyncio.run(run_random(config))
    print(summary)

    import numpy as np

    for npz_name in summary["npz_files"]:
        data = np.load(config.output_dir / npz_name)
        print(
            npz_name,
            "results:", data["measurement_results"].shape,  # (settings, shots, n_meas)
            "settings:", data["measurement_settings"].shape,  # (settings, n_meas, 2, 2)
            "trivial:", "trivial_measurement_results" in data,
        )


if __name__ == "__main__":
    main()
