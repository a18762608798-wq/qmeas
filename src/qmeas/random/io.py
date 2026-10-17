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
    trivial_plain_counts=None,
    tag=None,
):
    """把一个 SettingRun 的原始计数与测量基写成 RandomMeasAdd 兼容的 npz。

    计数存直方图（hist-v1，`format_version == 1`），不再存展开矩阵：
    每个 setting 的去重 bitstring 编成整数 key（bit j = 旧展开矩阵第 j 列，
    即 meas_indices 展平顺序第 j 个比特），`hist_keys`/`hist_counts` 按
    `hist_offsets` 切分。settings/binds 与旧格式完全相同。
    trivial_plain_counts 非空时多存一份配对实验一的不带 u_T 的 plain
    标定计数（hamming Z_T 缓解用，见 RandomMeasAdd）：键
    `trivial_plain_hist_*`；其 settings/binds 与
    `trivial_hist_*` 完全相同（角度绑定复用），故不另存。
    """
    num_settings = setting_run.num_settings
    num_shots = setting_run.num_shots
    n_meas = sum(len(group) for group in config.meas_indices)

    hist_keys, hist_counts, hist_offsets = counts_to_hist(
        counts, num_settings, num_shots, n_meas
    )
    group_data = {
        "format_version": np.int64(1),
        "measurement_hist_keys": hist_keys,
        "measurement_hist_counts": hist_counts,
        "measurement_hist_offsets": hist_offsets,
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
        t_keys, t_counts, t_offsets = counts_to_hist(
            trivial_counts, num_settings, trivial_num_shots, n_meas
        )
        group_data["trivial_hist_keys"] = t_keys
        group_data["trivial_hist_counts"] = t_counts
        group_data["trivial_hist_offsets"] = t_offsets
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

    if trivial_plain_counts is not None:
        p_keys, p_counts, p_offsets = counts_to_hist(
            trivial_plain_counts, num_settings, trivial_num_shots, n_meas
        )
        group_data["trivial_plain_hist_keys"] = p_keys
        group_data["trivial_plain_hist_counts"] = p_counts
        group_data["trivial_plain_hist_offsets"] = p_offsets

    config.output_dir.mkdir(parents=True, exist_ok=True)
    stem = config.name if tag is None else f"{config.name}_{tag}"
    filepath = (
        config.output_dir
        / f"{stem}_setting{run_idx}_settings{num_settings}_shots{num_shots}.npz"
    )
    np.savez(filepath, **group_data)
    return filepath


def counts_to_hist(counts, num_settings, num_shots, n_meas):
    """把每 setting 的计数直方图存成扁平 key/count 数组（hist-v1）。

    key 编码：整数的 bit j = 旧 `counts_to_results` 展开矩阵第 j 列，
    即 meas_indices 展平顺序第 j 个比特的值。bitstring 仍按 qiskit
    little-endian 解读（最左字符为最高位 clbit，左侧补 0 到 n_meas）。
    `hist_offsets` 长 num_settings+1，第 s 个 setting 切片为
    `keys[offsets[s]:offsets[s+1]]`；每 setting 内按 key 升序（bitstring
    字符串序与 key 数值序不一致，故按 key 排，与旧展开的行顺序无关，
    multiset 一致）。
    """
    if n_meas > 64:
        raise ValueError(f"hist key 存成 uint64，n_meas={n_meas} 超限")
    all_keys: list[int] = []
    all_counts: list[int] = []
    offsets = np.zeros(num_settings + 1, dtype=np.int64)
    for s, hist in enumerate(counts):
        keyed = []
        for bits, count in hist.items():
            col = bits.zfill(n_meas)[::-1]
            key = 0
            for j, c in enumerate(col):
                if c == "1":
                    key |= 1 << j
            keyed.append((key, count))
        keyed.sort()
        total = 0
        for key, count in keyed:
            all_keys.append(key)
            all_counts.append(count)
            total += count
        assert total == num_shots, (
            f"setting {s}: counts 合计 {total} != shots {num_shots}"
        )
        offsets[s + 1] = len(all_keys)
    return (
        np.asarray(all_keys, dtype=np.uint64),
        np.asarray(all_counts, dtype=np.int64),
        offsets,
    )


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


def write_summary(config, npz_paths, *, pair_info=None, manifest_file=None):
    """轻量 json: 只含字符串与结构信息, 计数与角度已进 npz。"""
    opts = config.runner_opts

    summary = {
        "runner": "aer" if isinstance(opts, AerOptions) else "quark",
        "format": "hist-v1",
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
    if manifest_file is not None:
        summary["quark_manifest"] = manifest_file
    if isinstance(opts, QuarkOptions):
        summary["chip"] = opts.chip
        summary["target_qubits"] = opts.target_qubits

    config.output_dir.mkdir(parents=True, exist_ok=True)
    with (config.output_dir / f"{config.name}.json").open(
        "w", encoding="utf-8"
    ) as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    return summary
