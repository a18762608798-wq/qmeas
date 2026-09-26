"""Quark 真机选机选比特示例：全部参数显式写出（含默认值）。

运行（项目根目录）: export QUARK_TOKEN=... && python example/benchmark/quark_benchmark_example.py
真实提交云端任务并轮询，会消耗机时；提交前脚本会先打印账单。
只想看流程不想花钱：把 DO_SUBMIT 设为 False，此时只拉拓扑、选链、打印账单。
"""

import asyncio
import os
from pathlib import Path

from qmeas.benchmark import (
    BenchmarkConfig,
    bill,
    build_chains,
    build_circuits,
    fetch_topology,
    recommend,
    run_benchmark,
    save_report,
)
from qmeas.random import QuarkOptions

# False 则只做离线部分（拓扑缓存→候选链→账单），不提交真机任务。
DO_SUBMIT = False

config = BenchmarkConfig(
    # 必填。只含 Baihua 与 Shenglian；Dongling 确认最差，不测。
    chips=["Baihua", "Shenglian"],
    # 必填。目标链长；当前基准电路（H + CZ 制备 + 奇偶稳定子）要求 ≥2。
    # ring 形状下必须为偶数（奇数直接拒绝，见下 shape 注释）。
    chain_length=8,
    # 每台机最多测几条候选链；每条链 = 4 个真机任务。
    max_chains_per_chip=3,
    # 每个电路的 shots；云端要求 1024 的整数倍。
    shots=1024,
    # 打分权重：chain_score = w_stab * mean(<S_i>) + w_ro * readout_fid，
    # 两者之和必须为 1；双比特门优先，故稳定子占大头。
    w_stab=0.8,
    w_ro=0.2,
    # 候选链扩展策略：conservative（一跳替换，最省） | slide（等长滑动）
    #   | multi-seed（邻近链长截长/补短成种） | sample（全图随机游走采样）。
    # 越往后候选越多、机时越多；sample 的静态预排序只定采样配额，不进最终排序。
    expansion="conservative",
    # 候选拓扑形状：chain（链，默认） | ring（环）。
    # ring 只支持偶数长度（奇数在网络调用前直接拒绝）；ring 下 expansion
    # 仅 sample 有效（conservative/slide/multi-seed 为链式思维，会明确报错）；
    # ring 自成一次推荐，只内部比较，不与 chain 混排。10 比特环示例：
    #   config = replace(config, shape="ring", chain_length=10,
    #                    expansion="sample", max_chains_per_chip=8)
    # （需 from dataclasses import replace；Baihua 实测有 4 个可用 10 环，
    # Shenglian 9 个，填不满上限时有多少测多少。）
    shape="chain",
    # 落盘根目录：拓扑缓存 topology_cache、断点 checkpoints、推荐报告 json。
    output_dir=Path(__file__).resolve().parent / "data_benchmark",
    # 任务名前缀与文件名 stem；checkpoint 文件名含它，中断续跑靠它认账。
    name="qubit_benchmark",
    # backend() 调用节流：连续两次实际拉取的最短间隔（秒）；命中缓存不计数。
    # 注意 backend() 在 import quark 前必须走 Agg 后端，否则无头环境永久阻塞，
    # 这一点 topology.py 已在内部处理，调用方不用管。
    fetch_throttle_interval=60.0,
    # 提交/轮询/重试基线；实际提交时按 (chip, target_qubits) 派生副本。
    # target_qubits/coupling_map 由 runner 按候选链自动填，这里保持默认即可。
    runner_opts=QuarkOptions(
        chip="Baihua",  # 占位，实际按候选链所属芯片覆盖
        token=os.environ.get("QUARK_TOKEN"),  # 默认 None 时读环境变量；勿写进代码提交
        target_qubits=[],  # 占位，实际按候选链覆盖
        mitigation=False,  # 基准电路固定结构，无需标定任务
        coupling_map=None,  # 占位，实际按链边 [[0,1],...] 覆盖，pin 住线性拓扑
        optimization_level=3,  # 裸测量比特由 runner 自动补 rz(0)，无需担心
        basis_gates=["rz", "rx", "ry", "cz"],  # 硬件原生门集（CZ 即两比特门基准对象）
        correct=False,  # 读出纠错透传，基准要测裸读出，保持 False
        max_submit_concurrency=10,  # 提交 QPS 上限（只限速不限总数）
        max_poll_concurrency=20,  # 轮询 QPS 上限
        poll_interval=10.0,  # 轮询间隔（秒）
        poll_timeout=24 * 3600.0,  # 单任务最长等待；None 为无限等（不推荐）
        submit_retries=6,  # 传输层失败的提交重试（指数退避）
        poll_retries=8,  # 传输层失败的轮询重试
        max_resubmits=3,  # 平台 error 时用原 QASM 重提新任务的次数
    ),
)


def show_offline_preview() -> dict:
    """离线预览：拉拓扑（带缓存）→选链→打印账单，不花一分钱机时。"""
    chains = {}
    for chip in config.chips:
        info = fetch_topology(config, chip)
        ch, dropped = build_chains(
            info, config.chain_length, config.max_chains_per_chip,
            config.expansion,
        )
        chains[chip] = ch
        print(f"{chip}: 校准 {info.get('calibration_time')}，"
              f"候选 {len(ch)} 条，丢弃 {len(dropped)} 条")
        for c in ch:
            print("   ", c)
    print("账单:", bill(config, chains))
    return chains


def main() -> None:
    chains = show_offline_preview()
    if not DO_SUBMIT:
        print("DO_SUBMIT=False，仅预览，未提交。确认账单后改 True 再跑。")
        return
    if config.runner_opts.token is None:
        raise RuntimeError("未找到 QUARK_TOKEN：请先 export QUARK_TOKEN=... 再运行")

    # 提交+轮询：async 并发；每份结果即时 checkpoint，中断重跑只补缺失。
    results = asyncio.run(run_benchmark(config, chains))

    # 打分排序：纯按实测分，排队深度只进 evidence 不参与排序。
    circuit_set = build_circuits(config.chain_length)
    chip, chain, evidence = recommend(
        results, circuit_set, config.w_stab, config.w_ro
    )
    path, _ = save_report(config, chip, chain, evidence)
    print("推荐:", chip, chain)
    print("报告:", path)

    # 推荐结果直接回填后续任务（以 random 为例，estimator 同理）：
    next_opts = QuarkOptions(chip=chip, target_qubits=chain)
    print("后续任务可用:", next_opts.chip, next_opts.target_qubits)


if __name__ == "__main__":
    main()
