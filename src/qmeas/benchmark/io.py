"""报告与推荐：跨机排序 + evidence 落盘。

recommend() 取全部 ChainResult 的首名；排序纯按实测分，
排队深度只记录不参与排序。
"""

import json

from .scoring import score_chain

__all__ = ["recommend", "save_report"]


def recommend(results, circuit_set, w_stab=0.8, w_ro=0.2):
    """打分排序并返回 (chip, target_qubits, evidence)。

    evidence 含每条链的稳定子均值、读出保真度、任务 id、
    快照校准时间与当时排队深度。
    """
    if not results:
        raise RuntimeError("无候选链结果，无法推荐")
    scored = []
    for r in results:
        if len(r.counts) < 4:
            continue
        m, f, s = score_chain(
            r.counts, circuit_set.stab_groups, r.shots, w_stab, w_ro
        )
        r.stab_mean, r.readout_fid, r.score = m, f, s
        scored.append(r)
    if not scored:
        raise RuntimeError("所有候选链均无有效测量结果，不返回猜测值")
    scored.sort(key=lambda r: r.score, reverse=True)
    best = scored[0]
    evidence = {
        "chains": [
            {
                "chip": r.chip,
                "chain": r.chain,
                "stab_mean": r.stab_mean,
                "readout_fid": r.readout_fid,
                "score": r.score,
                "tids": r.tids,
                "calibration_time": r.calibration_time,
                "queue_depth": r.queue_depth,
            }
            for r in scored
        ],
        "weights": {"w_stab": w_stab, "w_ro": w_ro},
    }
    return best.chip, list(best.chain), evidence


def save_report(config, chip, chain, evidence):
    """推荐报告落盘，返回 (json 路径，报告 dict)。"""
    report = {
        "name": config.name,
        "recommendation": {"chip": chip, "target_qubits": chain},
        "evidence": evidence,
    }
    config.output_dir.mkdir(parents=True, exist_ok=True)
    path = config.output_dir / f"{config.name}_recommendation.json"
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    tmp.replace(path)
    return path, report
