"""打分：稳定子均值（主） + 实测读出保真度（辅）。

chain_score = w_stab * mean(<S_i>) + w_ro * readout_fid。
期望恢复复用 estimator 的 QubitwiseBasis.recover。
"""

from dataclasses import dataclass, field

from ..estimator.basis import QubitwiseBasis

__all__ = ["ChainResult", "score_chain"]

_rec = QubitwiseBasis()


@dataclass
class ChainResult:
    chip: str
    chain: list[int]
    shots: int
    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    tids: dict[str, int] = field(default_factory=dict)
    stab_mean: float | None = None
    readout_fid: float | None = None
    score: float | None = None
    calibration_time: str | None = None
    queue_depth: int | str | None = None


def _readout_fid(counts: dict[str, dict[str, int]], shots: int) -> float:
    n = None
    for hist in list(counts.values())[:1]:
        for bits in hist:
            n = len(bits)
            break
    if n is None:
        raise ValueError("读出计数字典为空")
    p0 = counts.get("allzero", {}).get("0" * n, 0) / shots
    p1 = counts.get("allone", {}).get("1" * n, 0) / shots
    return (p0 + p1) / 2.0


def score_chain(counts, stab_groups, shots, w_stab=0.8, w_ro=0.2):
    """由 4 份计数字典算 (stab_mean, readout_fid, score)。"""
    evs = {}
    for key in ("stab_g0", "stab_g1"):
        evs.update(_rec.recover(stab_groups[key], counts[key], shots))
    stab_mean = sum(evs.values()) / len(evs)
    fid = _readout_fid(counts, shots)
    return stab_mean, fid, w_stab * stab_mean + w_ro * fid
