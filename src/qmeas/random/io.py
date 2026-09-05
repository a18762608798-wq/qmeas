import json

import numpy as np

from .config import AerOptions, QuarkOptions


def save_npz(
    config,
    run_idx,
    setting_run,
    binds,
    counts,
    *,
    trivial_binds,
    trivial_counts,
    trivial_num_shots,
    tag=None,
):
    """把一个 SettingRun 的原始计数与测量基写成 RandomMeas.jl 兼容的 npz。"""
    num_settings = setting_run.num_settings
    num_shots = setting_run.num_shots
    n_meas = sum(len(group) for group in config.meas_indices)

    group_data = {
        "measurement_results": counts_to_results(
            counts, num_settings, num_shots, n_meas
        ),
        "measurement_settings": build_settings(
            binds, config.params, num_settings, config.meas_indices
        ),
        "theta": binds_to_matrix(binds, config.params, "theta"),
        "phi": binds_to_matrix(binds, config.params, "phi"),
        "meas_indices": np.asarray(
            [q for group in config.meas_indices for q in group], dtype=np.int64
        ),
        "group_sizes": np.asarray(
            [len(group) for group in config.meas_indices], dtype=np.int64
        ),
        "num_settings": num_settings,
        "num_shots": num_shots,
        "n_meas": n_meas,
        "num_qubits": config.qc.num_qubits,
        "num_clbits": n_meas,
    }

    if trivial_counts is not None:
        group_data["trivial_measurement_results"] = counts_to_results(
            trivial_counts, num_settings, trivial_num_shots, n_meas
        )
        group_data["trivial_measurement_settings"] = build_settings(
            trivial_binds, config.params, num_settings, config.meas_indices
        )
        group_data["trivial_num_shots"] = trivial_num_shots
        group_data["trivial_theta"] = binds_to_matrix(
            trivial_binds, config.params, "theta"
        )
        group_data["trivial_phi"] = binds_to_matrix(
            trivial_binds, config.params, "phi"
        )

    config.output_dir.mkdir(parents=True, exist_ok=True)
    stem = config.name if tag is None else f"{config.name}_{tag}"
    filepath = (
        config.output_dir
        / f"{stem}_setting{run_idx}_settings{num_settings}_shots{num_shots}.npz"
    )
    np.savez(filepath, **group_data)
    return filepath


def counts_to_results(counts, num_settings, num_shots, n_meas):
    """把每 setting 的计数直方图展开成 (num_settings, num_shots, n_meas) 的 0/1 数组。

    qiskit (以及 quark 的 compiler=qiskit) 返回的 bitstring 是 little-endian:
    最左字符对应最高位 clbit。左侧补 0 到 n_meas 后反转, 使第 i 列对应
    meas_indices 展平顺序中的第 i 个比特。
    """
    results = np.zeros((num_settings, num_shots, n_meas), dtype=np.uint8)
    for s, hist in enumerate(counts):
        row = 0
        for bits, count in sorted(hist.items()):
            vec = np.frombuffer(
                bits.zfill(n_meas)[::-1].encode("ascii"), dtype=np.uint8
            ) - ord("0")
            results[s, row : row + count, :] = vec
            row += count
        assert row == num_shots, f"setting {s}: counts 合计 {row} != shots {num_shots}"
    return results


def build_settings(binds, params, num_settings, meas_indices):
    """每个 setting 每比特的 2x2 测量基矩阵。

    存电路实际施加的门 qc.u(-θ, 0, -φ) 的数值矩阵:
        [[cos(θ/2), e^{-iφ} sin(θ/2)], [-sin(θ/2), e^{-iφ} cos(θ/2)]]
    即 RandomMeas.jl 中 basis_transformation 的约定。
    """
    theta, phi = params
    n_meas = sum(len(group) for group in meas_indices)
    settings = np.zeros((num_settings, n_meas, 2, 2), dtype=np.complex128)

    col = 0
    for g, group in enumerate(meas_indices):
        th = np.asarray(binds[theta[g]])
        ph = np.asarray(binds[phi[g]])
        cos_half = np.cos(th / 2.0)
        sin_half = np.sin(th / 2.0)
        phase = np.exp(-1j * ph)

        u = np.empty((num_settings, 2, 2), dtype=np.complex128)
        u[:, 0, 0] = cos_half
        u[:, 0, 1] = phase * sin_half
        u[:, 1, 0] = -sin_half
        u[:, 1, 1] = phase * cos_half

        for _ in group:
            settings[:, col, :, :] = u
            col += 1
    return settings


def binds_to_matrix(binds, params, name):
    """把 ParameterVector 的绑定值整理成 (num_settings, n_groups) 数组。"""
    pvec = next(p for p in params if p.name == name)
    return np.asarray([binds[p] for p in pvec], dtype=np.float64).T


def write_summary(config, npz_paths, *, pair_info=None):
    """轻量 json: 只含字符串与结构信息, 计数与角度已进 npz。"""
    opts = config.runner_opts

    summary = {
        "runner": "aer" if isinstance(opts, AerOptions) else "quark",
        "ensemble": config.ensemble,
        "setting_runs": [
            (sr.num_settings, sr.num_shots) for sr in config.setting_runs
        ],
        "num_qubits": config.qc.num_qubits,
        "num_clbits": sum(len(group) for group in config.meas_indices),
        "meas_indices": config.meas_indices,
        "npz_files": [p.name for p in npz_paths],
    }
    if pair_info is not None:
        summary["conjugate_pair"] = pair_info
    if isinstance(opts, QuarkOptions):
        summary["chip"] = opts.chip
        summary["target_qubits"] = opts.target_qubits

    config.output_dir.mkdir(parents=True, exist_ok=True)
    with (config.output_dir / f"{config.name}.json").open(
        "w", encoding="utf-8"
    ) as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    return summary
