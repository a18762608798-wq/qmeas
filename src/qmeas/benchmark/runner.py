"""基准执行：async 并发提交/轮询 + 逐份结果即时 checkpoint。

async 只管速度（提交与轮询的并发），checkpoint 管崩溃不丢数：
每份电路结果返回即原子落盘；重跑时跳过已有结果只补缺失。
网络层（限流/重试/重提）复用 random.quark_client。
"""

import asyncio
import json
from dataclasses import replace

from qiskit import qasm2, transpile

from ..random.runner import _guard_empty_qubits
from .circuits import CIRCUIT_KEYS, build_circuits
from .config import BenchmarkConfig
from .scoring import ChainResult
from .topology import build_chains, fetch_topology

__all__ = ["run_benchmark", "bill"]


def bill(config: BenchmarkConfig, chains: dict[str, list[list[int]]]) -> dict:
    """账单：提交前的任务数/shots 预览（4.2 要求先打印再提交）。"""
    per_chain = len(CIRCUIT_KEYS)
    total = sum(len(v) for v in chains.values()) * per_chain
    return {
        "chips": {
            chip: {"chains": len(v), "tasks": len(v) * per_chain}
            for chip, v in chains.items()
        },
        "total_tasks": total,
        "shots_per_task": config.shots,
        "total_shots": total * config.shots,
    }


def _ckpt_path(config: BenchmarkConfig, chip: str, ci: int):
    return config.output_dir / "checkpoints" / f"{config.name}_{chip}_c{ci}.json"


def _load_ckpt(config: BenchmarkConfig, chip: str, ci: int):
    p = _ckpt_path(config, chip, ci)
    if not p.is_file():
        return {}, {}
    with p.open(encoding="utf-8") as f:
        d = json.load(f)
    return d.get("counts", {}), d.get("tids", {})


def _save_ckpt(config: BenchmarkConfig, chip: str, ci: int, chain, counts, tids):
    p = _ckpt_path(config, chip, ci)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(
            {"chip": chip, "chain": chain, "counts": counts, "tids": tids},
            f,
            ensure_ascii=False,
        )
    tmp.replace(p)


def _transpile_qasm(qc, opts):
    basic = transpile(
        qc,
        basis_gates=opts.basis_gates,
        optimization_level=opts.optimization_level,
        coupling_map=opts.coupling_map,
    )
    return qasm2.dumps(_guard_empty_qubits(basic))


async def run_benchmark(config: BenchmarkConfig, chains=None) -> list[ChainResult]:
    """对给定候选链跑完全部 4 电路基准，返回 ChainResult 列表。

    chains 为 None 时由拓扑快照自动生成（每机 ≤ max_chains_per_chip）。
    """
    from quark import Task

    from ..random.quark_client import (
        await_quark,
        make_quark_task,
        quark_token,
        submit_quark,
    )

    base_opts = config.runner_opts
    token = quark_token(base_opts)
    tmgr = make_quark_task(token, base_opts.submit_retries)
    submit_sem = asyncio.Semaphore(base_opts.max_submit_concurrency)
    poll_sem = asyncio.Semaphore(base_opts.max_poll_concurrency)
    try:
        queue = tmgr.status()
    except Exception:
        queue = {}

    if chains is None:
        chains = {}
        for chip in config.chips:
            info = fetch_topology(config, chip)
            ch, _ = build_chains(
                info, config.chain_length, config.max_chains_per_chip,
                config.expansion, config.shape,
            )
            chains[chip] = ch
    calibs = {}
    for chip in chains:
        try:
            calibs[chip] = fetch_topology(config, chip).get("calibration_time")
        except Exception:
            calibs[chip] = None

    cset = build_circuits(config.chain_length, ring=config.shape == "ring")

    # 阶段一：全部缺失电路一次转译、一次提交，tids 即时落盘占住队列。
    jobs = []
    for chip, chip_chains in chains.items():
        for ci, chain in enumerate(chip_chains):
            edges = [[i, i + 1] for i in range(len(chain) - 1)]
            if config.shape == "ring":
                edges.append([len(chain) - 1, 0])
            opts = replace(
                base_opts,
                chip=chip,
                target_qubits=list(chain),
                coupling_map=edges,
            )
            counts, tids = _load_ckpt(config, chip, ci)
            missing = [k for k in CIRCUIT_KEYS if k not in counts]
            # 断点续跑不重复消耗机时：已有 tid 无结果的只轮询，不重提；
            # 只有无 tid 的才转译提交。
            to_submit = [k for k in missing if k not in tids]
            jobs.append({
                "chip": chip, "ci": ci, "chain": list(chain),
                "opts": opts, "counts": counts, "tids": tids,
                "missing": missing, "to_submit": to_submit, "qasms": {},
            })

    async def _transpile_job(job, key):
        # 本地 CPU 活：限并发防线程爆炸，进度打印方便定位卡点
        async with transpile_sem:
            qasm = await asyncio.to_thread(
                _transpile_qasm, cset.circuits[key], job["opts"]
            )
            _transpile_job.done += 1
            if _transpile_job.done % 500 == 0 or _transpile_job.done == _transpile_job.total:
                print(f"转译 { _transpile_job.done}/{_transpile_job.total}",
                      flush=True)
            return qasm

    _transpile_job.done = 0
    _transpile_job.total = sum(len(j["missing"]) for j in jobs)
    transpile_sem = asyncio.Semaphore(16)
    print(f"转译开始，共 {_transpile_job.total} 个电路", flush=True)

    t_tasks = [
        asyncio.create_task(_transpile_job(job, key))
        for job in jobs for key in job["missing"]
    ]
    if t_tasks:
        # gather 按创建顺序返回：按 jobs/missing 顺序回填
        out = await asyncio.gather(*t_tasks)
        it = iter(out)
        for job in jobs:
            for key in job["missing"]:
                job["qasms"][key] = next(it)

    async def _submit_one(job, key):
        async with submit_sem:
            return await asyncio.to_thread(
                submit_quark,
                tmgr,
                job["opts"],
                job["qasms"][key],
                config.shots,
                f"{config.name}_{job['chip']}_c{job['ci']}_{key}",
            )

    s_tasks = [
        asyncio.create_task(_submit_one(job, key))
        for job in jobs for key in job["to_submit"]
    ]
    if s_tasks:
        print(f"提交开始，共 {len(s_tasks)} 个任务", flush=True)
        tids_got = await asyncio.gather(*s_tasks)
        print("提交完毕，tids 已落盘，转入统一轮询", flush=True)
        it = iter(tids_got)
        for job in jobs:
            for key in job["to_submit"]:
                job["tids"][key] = next(it)
            # tids 即时落盘：提交与轮询之间被杀也不丢 tid
            _save_ckpt(config, job["chip"], job["ci"], job["chain"],
                       job["counts"], job["tids"])

    # 阶段二：统一轮询，完成一份落盘一份（as_completed 逐个回填）。
    async def _poll_one(job, key):
        async with poll_sem:
            hist = await await_quark(
                tmgr,
                job["tids"][key],
                job["opts"],
                submit_sem,
                job["qasms"].get(key, ""),
                config.shots,
                f"{config.name}_{job['chip']}_c{job['ci']}_{key}",
                attempt_tag=f"{job['chip']}_c{job['ci']}",
            )
            return id(job), key, hist

    by_id = {id(job): job for job in jobs}
    p_tasks = [
        asyncio.create_task(_poll_one(job, key))
        for job in jobs for key in job["missing"]
    ]
    try:
        for coro in asyncio.as_completed(p_tasks):
            jid, key, hist = await coro
            job = by_id[jid]
            job["counts"][key] = hist
            _save_ckpt(config, job["chip"], job["ci"], job["chain"],
                       job["counts"], job["tids"])
    except Exception:
        for t in p_tasks:
            t.cancel()
        raise

    results = [
        ChainResult(
            chip=job["chip"],
            chain=list(job["chain"]),
            shots=config.shots,
            counts=dict(job["counts"]),
            tids={k: int(v) for k, v in job["tids"].items()},
            calibration_time=calibs.get(job["chip"]),
            queue_depth=queue.get(job["chip"]),
        )
        for job in jobs
    ]
    return results
