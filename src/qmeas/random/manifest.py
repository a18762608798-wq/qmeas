"""Quark 中间态持久化: tid 清单 + 已收 counts, 随收随存, 中断可续跑。

文件: ``{output_dir}/{name}.quark_manifest.json``, 结构::

    {
      "meta": {"name": ..., "seed": ..., "ensemble": ..., "setting_runs": [...],
               "chip": ..., "target_qubits": [...], "mitigation": ...,
               "conjugate_pair_i1": [...] | None},
      "tasks": {
        "<task_name>": {"tid": int, "history": [int, ...],
                        "status": "submitted" | "done",
                        "shots": int, "counts": {bitstring: int} | None},
        ...
      }
    }

- ``task_name`` 即提交时的 ``name``（如 ``my_job_setting0_U3`` /
  ``my_job_setting0_calib_U3``）, 跨重跑稳定, 可直接作为逻辑主键。
- ``submitted`` = 已提交、结果还没收回来（中断后凭 ``tid`` 手动
  ``tmgr.result(tid)`` 也能取回）; ``done`` = 已收回（``counts`` 非空）。
- meta 不一致（换 seed/设置/chip 等）说明 binds 已变、旧 tid 不可复用,
  调用方应备份旧文件后重起。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

__all__ = [
    "manifest_path",
    "manifest_meta",
    "load_manifest",
    "atomic_save_manifest",
    "is_compatible",
    "pending_tids",
]


def manifest_path(output_dir, name) -> Path:
    return Path(output_dir) / f"{name}.quark_manifest.json"


def manifest_meta(config) -> dict:
    opts = config.runner_opts
    pair = config.conjugate_pair
    return {
        "name": config.name,
        "seed": config.seed,
        "ensemble": config.ensemble,
        "setting_runs": [
            [sr.num_settings, sr.num_shots] for sr in config.setting_runs
        ],
        "chip": getattr(opts, "chip", None),
        "target_qubits": list(getattr(opts, "target_qubits", None) or []),
        "mitigation": bool(getattr(opts, "mitigation", False)),
        "conjugate_pair_i1": list(pair.i1_groups) if pair is not None else None,
    }


def load_manifest(path) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def atomic_save_manifest(path, data) -> None:
    """原子落盘: 写临时文件后 rename, 避免中断留下半截 json。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def is_compatible(manifest: dict | None, config) -> bool:
    if not manifest or "meta" not in manifest or "tasks" not in manifest:
        return False
    return manifest["meta"] == manifest_meta(config)


def backup_incompatible(path) -> Path:
    """meta 对不上的旧 manifest 改名备份, 返回备份路径。"""
    path = Path(path)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    bak = path.with_name(f"{path.stem}.bak_{stamp}.json")
    os.replace(path, bak)
    return bak


def pending_tids(output_dir, name) -> list[tuple[str, int]]:
    """已提交但还没收回结果的 (task_name, tid) 清单, 供中断后手动取数。"""
    m = load_manifest(manifest_path(output_dir, name))
    if not m:
        return []
    return [
        (task_name, entry["tid"])
        for task_name, entry in m.get("tasks", {}).items()
        if entry.get("status") == "submitted" and entry.get("tid") is not None
    ]
