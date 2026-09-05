import asyncio
import os

from qiskit import qasm2, transpile
from qiskit.circuit import CircuitInstruction
from qiskit.circuit.library import RZGate
from qiskit_aer.primitives import EstimatorV2
from quark import Task

from .basis import (
    QubitwiseBasis,
    add_meas,
    bind_group_rotation,
    group_qubitwise,
    rebuild_op_vals,
)
from .config import AerEstimatorOptions, EstimatorConfig, QuarkEstimatorOptions


async def run_estimator(config):
    if isinstance(config.runner_opts, AerEstimatorOptions):
        return await _run_aer(config)
    return await _run_quark(config)


# ── Aer ────────────────────────────────────────────────────────────


async def _run_aer(config):
    estimator = EstimatorV2(
        options={
            "backend_options": {"method": config.runner_opts.method},
        }
    )
    pubs = [(config.qc.decompose(), ob) for ob in config.observables]
    job = estimator.run(pubs)
    result = job.result()
    evs = [r.data.evs for r in result]
    return {"evs": evs}


# ── Quark (qubitwise) ──────────────────────────────────────────────


async def _run_quark(config):
    opts = config.runner_opts
    token = opts.token or os.environ["QUARK_TOKEN"]

    groups, meas_pauli_ls = group_qubitwise(config.observables)
    qc_with_params, theta_x, theta_y = add_meas(config.qc.copy(), config.qc.num_qubits)

    # 整个任务只构造一次 Task(单例), 避免每组重复 verify() 请求
    tmgr = Task(token)

    tids = []
    for group_idx in range(len(groups)):
        bound_qc = bind_group_rotation(
            qc_with_params, group_idx, meas_pauli_ls, theta_x, theta_y
        )
        tid = await _submit_quark(bound_qc, tmgr, opts, f"{opts.name}_g{group_idx}")
        tids.append(tid)

    # 全部测量基提交后统一等待; 任一任务失败则取消其余轮询并抛出
    awaiters = [asyncio.create_task(_await_quark(tmgr, tid)) for tid in tids]
    try:
        res_ls = await asyncio.gather(*awaiters)
    except Exception:
        for a in awaiters:
            a.cancel()
        raise

    basis = QubitwiseBasis()
    expval_map = {}
    for group_idx, hist in enumerate(res_ls):
        expects = basis.recover(groups[group_idx], hist, opts.shots)
        expval_map.update(expects)

    evs = rebuild_op_vals(config.observables, expval_map)
    return {"evs": evs}


# ── Quark submit ───────────────────────────────────────────────────


def _guard_empty_qubits(qc):
    """Quark 平台缺陷 workaround: 防止"裸测量比特"。

    quark 对"上面没有任何门、只有测量指令"的比特会直接报错。而上面的 transpile
    在 optimization_level=3 下会把恒等门优化删除, 例如:
      * `basis._group_params` 对 I/Z 分量绑定 rx=ry=0, 即恒等门;
      * 原电路 `config.qc` 本身未触及的比特。
    一旦某个比特在测量前没有任何门, quark 就会报错。

    这里扫描 transpile 后的电路, 给这类比特在测量前原地插入一个 rz(0):
    rz(0) 是允许的基底门、严格物理恒等, 不改变任何测量统计。
    """
    gated = set()
    for inst in qc.data:
        if inst.operation.name != "measure":
            gated.update(qc.find_bit(q).index for q in inst.qubits)

    guarded = set()
    pos = 0
    while pos < len(qc.data):
        inst = qc.data[pos]
        if inst.operation.name == "measure":
            for q in inst.qubits:
                i = qc.find_bit(q).index
                if i not in gated and i not in guarded:
                    qc.data.insert(
                        pos, CircuitInstruction(RZGate(0.0), (qc.qubits[i],), ())
                    )
                    guarded.add(i)
                    pos += 1
        pos += 1
    return qc


async def _submit_quark(qc, tmgr, opts, name):
    """构造并提交 quark 任务, 返回 tid(不再等待结果)。"""
    basic_qc = transpile(
        qc,
        basis_gates=opts.basis_gates,
        optimization_level=opts.optimization_level,
        coupling_map=opts.coupling_map,
    )
    basic_qc = _guard_empty_qubits(basic_qc)
    task = {
        "chip": opts.chip,
        "shots": opts.shots,
        "name": name,
        "circuit": qasm2.dumps(basic_qc),
        "options": {
            "compiler": opts.compiler,
            "correct": opts.correct,
            "target_qubits": opts.target_qubits,
        },
    }
    tid = tmgr.run(task)
    return tid


async def _await_quark(tmgr, tid):
    """轮询任务结果, 返回计数字典。

    排队/运行中平台返回非空 dict 但缺 "count" 键 (error 为空);
    若 "error" 有实际内容表示平台失败, 打印后继续轮询,
    直到拿到含 "count" 的结果。
    """
    res = {}
    while "count" not in res:
        await asyncio.sleep(10)
        res = tmgr.result(tid)
        if res.get("error"):
            print(f"quark 任务 {tid}: {res['error']}")
    return res["count"]
