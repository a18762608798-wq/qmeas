import asyncio
from dataclasses import dataclass

import numpy as np
from qiskit import QuantumCircuit, qasm2, transpile
from qiskit.circuit import CircuitInstruction, Clbit
from qiskit.circuit.library import RZGate
from qiskit_aer import AerSimulator

from .config import AerOptions, QuarkOptions, RandomMeasConfig
from .ensemble import conjugate_binds, create_parameter_generator
from .io import save_npz, write_summary
from .manifest import (
    atomic_save_manifest,
    backup_incompatible,
    is_compatible,
    load_manifest,
    manifest_meta,
    manifest_path,
)
from .quark_client import await_quark, make_quark_task, quark_token, submit_quark

Counts = dict[str, int]


@dataclass
class RunResult:
    counts: list[Counts]
    trivial_counts: list[Counts] | None = None
    # 配对实验一的不带 u_T 的 plain 标定计数（hamming Z_T 缓解用）；
    # 其余情形为 None。settings 与 binds 复用 trivial_*（角度绑定相同）。
    trivial_plain_counts: list[Counts] | None = None


async def run_random(config: RandomMeasConfig) -> dict:
    """随机测量管线: 采样角度、加旋转门、按 setting 执行, 落盘为 npz + 轻量 json。"""
    # 共轭配对（Z_T）：实验一在随机旋转前先对 I_1 区加 u_T（σ^y），
    # trivial 标定与主电路保持完全相同的门结构
    pair = config.conjugate_pair
    ut_qubits = (
        [q for g in pair.i1_groups for q in config.meas_indices[g]]
        if pair is not None
        else []
    )
    qc_exp1 = config.qc.copy()
    for q in ut_qubits:
        qc_exp1.y(q)
    qc_meas_1 = add_meas(qc_exp1, config.params, config.meas_indices)
    qc_meas_2 = (
        add_meas(config.qc.copy(), config.params, config.meas_indices)
        if pair is not None
        else qc_meas_1
    )

    # 每个 SettingRun 一个独立子流: 各自可复现, 互不干扰
    param_gens = [
        create_parameter_generator(config.ensemble, seed=c)
        for c in np.random.SeedSequence(config.seed).spawn(len(config.setting_runs))
    ]
    bind_groups = [
        gen.generate(config.params, sr.num_settings)
        for gen, sr in zip(param_gens, config.setting_runs)
    ]
    # 共轭配对（Z_T）：实验二的 binds 由实验一经 φ 取反得到，设置逐行对应
    bind_groups_2 = (
        [conjugate_binds(b, config.params, pair.i1_groups) for b in bind_groups]
        if pair is not None
        else None
    )
    trivial_qc_1 = _prepare_trivial_qc(config, extra_y_qubits=ut_qubits)
    trivial_qc_2 = (
        _prepare_trivial_qc(config) if pair is not None else trivial_qc_1
    )
    # 配对实验一的 plain 标定（不带 u_T，hamming Z_T 缓解用）：
    # trivial_1 含 u_T（门结构与主电路一致，供 shadow 用），
    # plain 版供 hamming 标定（要求 |0> 态，见 RandomMeasAdd）。
    # 非配对时为 None，不跑。
    trivial_qc_1_plain = (
        _prepare_trivial_qc(config)
        if (pair is not None and trivial_qc_1 is not None)
        else None
    )

    # 真并发: 先把全部 SettingRun（含 exp1/exp2）建成协程, 一次 gather 发出去,
    # 循环体内不 await, 避免串行等待。
    # Quark 路径全 run 共享同一对信号量与同一个 Task：只限速度（提交/轮询 QPS）、
    # 不限总数；同时把每次线程新建 Task 的 /task/verify 洪峰降为 O(1)。
    # Quark 路径同时共享一份落盘 manifest（tid + 已收 counts，随收随存）：
    # 重跑同名任务自动复用已提交 tid、只补提缺失，脚本中断后也可凭 manifest
    # 里的 tid 手动取数；meta 对不上（换 seed/设置/chip 等）则备份旧文件重起。
    submit_sem = poll_sem = tmgr = None
    mpath = mdata = mlock = None
    if isinstance(config.runner_opts, QuarkOptions):
        submit_sem = asyncio.Semaphore(config.runner_opts.max_submit_concurrency)
        poll_sem = asyncio.Semaphore(config.runner_opts.max_poll_concurrency)
        tmgr = make_quark_task(
            quark_token(config.runner_opts), config.runner_opts.submit_retries
        )
        mpath = manifest_path(config.output_dir, config.name)
        mdata = load_manifest(mpath)
        if mdata is not None and not is_compatible(mdata, config):
            bak = backup_incompatible(mpath)
            print(f"quark manifest 与当前配置不一致，已备份 {bak.name}，重新提交")
            mdata = None
        if mdata is None:
            mdata = {"meta": manifest_meta(config), "tasks": {}}
        mlock = asyncio.Lock()
        n_done = sum(1 for e in mdata["tasks"].values() if e.get("status") == "done")
        n_pend = sum(
            1 for e in mdata["tasks"].values() if e.get("status") == "submitted"
        )
        if n_done or n_pend:
            print(f"quark manifest 命中: done={n_done} 待轮询={n_pend}，自动续跑")
    jobs = []
    for run_idx, setting_run in enumerate(config.setting_runs):
        jobs.append(
            _run_one(
                config.runner_opts,
                qc_meas_1,
                bind_groups[run_idx],
                setting_run,
                name=f"{config.name}_setting{run_idx}",
                trivial_qc=trivial_qc_1,
                trivial_qc_plain=trivial_qc_1_plain,
                _submit_sem=submit_sem,
                _poll_sem=poll_sem,
                _tmgr=tmgr,
                _manifest=mdata,
                _manifest_path=mpath,
                _manifest_lock=mlock,
            )
        )
        if pair is not None:
            jobs.append(
                _run_one(
                    config.runner_opts,
                    qc_meas_2,
                    bind_groups_2[run_idx],
                    setting_run,
                    name=f"{config.name}_exp2_setting{run_idx}",
                    trivial_qc=trivial_qc_2,
                    _submit_sem=submit_sem,
                    _poll_sem=poll_sem,
                    _tmgr=tmgr,
                    _manifest=mdata,
                    _manifest_path=mpath,
                    _manifest_lock=mlock,
                )
            )
    if mpath is None:
        all_results = await asyncio.gather(*jobs)
    else:
        # Quark：显式 task，任一失败即 fail-fast，同时保证已拿到的 tid 不丢：
        # 先取消兄弟协程（其中 _save 的 dict 更新先于落盘，取消不丢内存态），
        # 再把内存快照兜底刷盘（绕开锁：teardown 中锁可能被取消持有）。
        # 中断后重跑同名任务自动续上。
        tasks = [asyncio.create_task(j) for j in jobs]
        try:
            all_results = await asyncio.gather(*tasks)
        except BaseException:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await asyncio.to_thread(
                    atomic_save_manifest, mpath,
                    {"meta": mdata["meta"], "tasks": dict(mdata.get("tasks", {}))},
                )
            except BaseException:
                pass
            raise
    if pair is None:
        run_results = list(all_results)
    else:
        run_results = list(all_results[0::2])
        run_results_2 = list(all_results[1::2])

    npz_paths = [
        save_npz(
            config,
            run_idx,
            setting_run,
            bind_groups[run_idx],
            run_results[run_idx].counts,
            trivial_binds=(bind_groups[run_idx] if trivial_qc_1 is not None else None),
            trivial_counts=run_results[run_idx].trivial_counts,
            trivial_num_shots=setting_run.num_shots if trivial_qc_1 is not None else None,
            trivial_plain_counts=run_results[run_idx].trivial_plain_counts,
            tag="exp1" if pair is not None else None,
        )
        for run_idx, setting_run in enumerate(config.setting_runs)
    ]
    pair_info = None
    if pair is not None:
        npz_paths_2 = [
            save_npz(
                config,
                run_idx,
                setting_run,
                bind_groups_2[run_idx],
                run_results_2[run_idx].counts,
                trivial_binds=(
                    bind_groups_2[run_idx] if trivial_qc_2 is not None else None
                ),
                trivial_counts=run_results_2[run_idx].trivial_counts,
                trivial_num_shots=setting_run.num_shots
                if trivial_qc_2 is not None
                else None,
                tag="exp2",
            )
            for run_idx, setting_run in enumerate(config.setting_runs)
        ]
        pair_info = {
            "i1_groups": list(pair.i1_groups),
            "exp1_ut_qubits": ut_qubits,
            "exp1_files": [p.name for p in npz_paths],
            "exp2_files": [p.name for p in npz_paths_2],
        }

    return write_summary(
        config, npz_paths, pair_info=pair_info,
        manifest_file=mpath.name if mpath is not None else None,
    )


async def _run_one(
    opts, qc, binds, setting_run, *, name, trivial_qc, trivial_qc_plain=None,
    _submit_sem=None, _poll_sem=None, _tmgr=None,
    _manifest=None, _manifest_path=None, _manifest_lock=None,
):
    # 唯一 await 分发: Aer 是纯阻塞同步计算, 丢进线程池不占事件循环;
    # Quark 自身是真异步协程, 直接 await。
    if isinstance(opts, AerOptions):
        return await asyncio.to_thread(
            _run_aer, opts, qc, binds, setting_run, name=name,
            trivial_qc=trivial_qc, trivial_qc_plain=trivial_qc_plain,
        )
    return await _run_quark(
        opts, qc, binds, setting_run, name=name, trivial_qc=trivial_qc,
        trivial_qc_plain=trivial_qc_plain,
        _submit_sem=_submit_sem, _poll_sem=_poll_sem, _tmgr=_tmgr,
        _manifest=_manifest, _manifest_path=_manifest_path,
        _manifest_lock=_manifest_lock,
    )


# ── Aer ────────────────────────────────────────────────────────────


def _run_aer(opts, qc, binds, setting_run, *, name, trivial_qc, trivial_qc_plain=None):
    simulator = AerSimulator(
        method=opts.method,
        device=opts.device,
        precision=opts.precision,
    )
    job = simulator.run(
        transpile(qc, simulator),
        shots=setting_run.num_shots,
        parameter_binds=[binds],
    )
    counts = [job.result().get_counts(i) for i in range(setting_run.num_settings)]
    if trivial_qc is None:
        return RunResult(counts=counts)

    trivial_job = simulator.run(
        transpile(trivial_qc, simulator),
        shots=setting_run.num_shots,
        parameter_binds=[binds],
    )
    trivial_counts = [
        trivial_job.result().get_counts(i) for i in range(setting_run.num_settings)
    ]
    if trivial_qc_plain is None:
        return RunResult(counts=counts, trivial_counts=trivial_counts)

    plain_job = simulator.run(
        transpile(trivial_qc_plain, simulator),
        shots=setting_run.num_shots,
        parameter_binds=[binds],
    )
    trivial_plain_counts = [
        plain_job.result().get_counts(i) for i in range(setting_run.num_settings)
    ]
    return RunResult(
        counts=counts,
        trivial_counts=trivial_counts,
        trivial_plain_counts=trivial_plain_counts,
    )


# ── Quark 编排（HTTP 细节见 quark_client） ─────────────────────────


async def _run_quark(
    opts, qc, binds, setting_run, *, name, trivial_qc, trivial_qc_plain=None,
    _submit_sem=None, _poll_sem=None, _tmgr=None,
    _manifest=None, _manifest_path=None, _manifest_lock=None,
):
    # transpile 是 CPU 阻塞工作, 丢进线程池; 主/标定两个批量一次 gather。
    if trivial_qc is not None:
        qasm_ls, trivial_qasm_ls = await asyncio.gather(
            asyncio.to_thread(_to_qasm2, qc, setting_run.num_settings, binds, opts),
            asyncio.to_thread(
                _to_qasm2, trivial_qc, setting_run.num_settings, binds, opts
            ),
        )
        plain_qasm_ls = (
            await asyncio.to_thread(
                _to_qasm2, trivial_qc_plain, setting_run.num_settings, binds, opts
            )
            if trivial_qc_plain is not None
            else None
        )
    else:
        qasm_ls = await asyncio.to_thread(
            _to_qasm2, qc, setting_run.num_settings, binds, opts
        )
        trivial_qasm_ls = None
        plain_qasm_ls = None

    # 未经 run_random 分发的直接调用：本地建默认限流与共享 Task，保证行为一致。
    # 此时无 manifest（中间态不落盘），行为与原来完全一致。
    if _submit_sem is None:
        _submit_sem = asyncio.Semaphore(opts.max_submit_concurrency)
    if _poll_sem is None:
        _poll_sem = asyncio.Semaphore(opts.max_poll_concurrency)
    if _tmgr is None:
        _tmgr = make_quark_task(quark_token(opts), opts.submit_retries)

    # 全量 spec（顺序即返回顺序：主/标定/plain 交错），供 error 重提用。
    # task_name 全局稳定，可作 manifest 主键，跨重跑复用 tid。
    full_specs = []  # (task_name, qasm_str, shots)
    for i, qasm_str in enumerate(qasm_ls):
        full_specs.append((f"{name}_U{i}", qasm_str, setting_run.num_shots))
        if trivial_qasm_ls is not None:
            full_specs.append(
                (f"{name}_calib_U{i}", trivial_qasm_ls[i], setting_run.num_shots)
            )
            if plain_qasm_ls is not None:
                full_specs.append(
                    (f"{name}_plain_U{i}", plain_qasm_ls[i], setting_run.num_shots)
                )

    use_manifest = _manifest is not None and _manifest_path is not None
    if not use_manifest:
        tids = list(
            await asyncio.gather(
                *[_submit_one(_submit_sem, _tmgr, opts, qasm, shots, tname)
                  for tname, qasm, shots in full_specs]
            )
        )
        counts = await _poll_all(
            _poll_sem, _tmgr, opts, _submit_sem, tids, full_specs, name
        )
    else:
        counts = await _submit_poll_resume(
            opts, full_specs, name,
            _submit_sem=_submit_sem, _poll_sem=_poll_sem, _tmgr=_tmgr,
            _manifest=_manifest, _manifest_path=_manifest_path,
            _manifest_lock=_manifest_lock,
        )

    if trivial_qasm_ls is None:
        return RunResult(counts=counts)
    if plain_qasm_ls is None:
        return RunResult(counts=counts[0::2], trivial_counts=counts[1::2])
    return RunResult(
        counts=counts[0::3],
        trivial_counts=counts[1::3],
        trivial_plain_counts=counts[2::3],
    )


async def _submit_one(submit_sem, tmgr, opts, qasm_str, shots, task_name):
    """提交是阻塞 HTTP，限流后由调用方一次 gather 发出去，循环体内不 await。"""
    async with submit_sem:
        return await asyncio.to_thread(
            submit_quark, tmgr, opts, qasm_str, shots, task_name
        )


async def _poll_all(poll_sem, tmgr, opts, submit_sem, tids, specs, name,
                    on_resubmit_factory=None):
    """轮询已是并发: 建 task 不 await, 一次 gather 等全部。"""
    # spec 统一为 (task_name, qasm_str, shots)。
    async def _poll_one_fixed(tid, spec):
        task_name, qasm_str, shots = spec
        async with poll_sem:
            cb = None
            if on_resubmit_factory is not None:
                cb = on_resubmit_factory(task_name)
            return await await_quark(
                tmgr, tid, opts, submit_sem, qasm_str, shots, task_name,
                attempt_tag=name, on_resubmit=cb,
            )

    awaiters = [
        asyncio.create_task(_poll_one_fixed(tid, spec))
        for tid, spec in zip(tids, specs)
    ]
    try:
        return list(await asyncio.gather(*awaiters))
    except Exception:
        for a in awaiters:
            a.cancel()
        raise


async def _submit_poll_resume(
    opts, full_specs, name, *, _submit_sem, _poll_sem, _tmgr,
    _manifest, _manifest_path, _manifest_lock,
):
    """断点续跑版提交/轮询：done 直接复用 counts，submitted 复用 tid 只轮询。

    - 每个 submit 完成即记 manifest（submitted），每个 poll 完成即记
      manifest（done + counts），error 重提的新 tid 也即时记入 history；
      任一时刻中断，重跑同名任务都只补缺失、不重提已有。
    - 返回顺序与 full_specs 一致，调用方按原交错顺序切分即可。
    """
    if _manifest_lock is None:
        _manifest_lock = asyncio.Lock()

    tasks = _manifest.setdefault("tasks", {})

    async def _save():
        # 快照 + 落盘全程持锁串行，避免并发 save 交错导致文件回退丢条目
        async with _manifest_lock:
            snapshot = {"meta": _manifest["meta"], "tasks": dict(tasks)}
            await asyncio.to_thread(atomic_save_manifest, _manifest_path, snapshot)

    def _done_entry(task_name):
        e = tasks.get(task_name)
        if e and e.get("status") == "done" and e.get("counts") is not None:
            return True
        return False

    cached = {}
    to_submit = []  # (task_name, qasm_str, shots)
    to_poll = []  # (task_name, tid, qasm_str, shots)
    for task_name, qasm_str, shots in full_specs:
        e = tasks.get(task_name)
        if _done_entry(task_name):
            cached[task_name] = e["counts"]
        elif e and e.get("tid") is not None and e.get("status") == "submitted":
            to_poll.append((task_name, e["tid"], qasm_str, shots))
        else:
            to_submit.append((task_name, qasm_str, shots))
    if cached or to_poll:
        print(f"{name}: manifest 复用 done={len(cached)} 待轮询={len(to_poll)} 待提交={len(to_submit)}")

    # 待提交：逐个提交、每成功一个即记 submitted（gather 内不 await，保证并发）。
    async def _submit_record(task_name, qasm_str, shots):
        tid = await _submit_one(_submit_sem, _tmgr, opts, qasm_str, shots, task_name)
        async with _manifest_lock:
            tasks[task_name] = {
                "tid": tid, "history": [tid],
                "status": "submitted", "shots": shots, "counts": None,
            }
        await _save()
        return task_name, tid

    new_tids = {}
    if to_submit:
        submit_jobs = [
            _submit_record(tname, qasm, shots) for tname, qasm, shots in to_submit
        ]
        # 全部落定再判错：已成功的 submit 都已记入 manifest（submitted），
        # 即使本批有失败，重跑也能续上，不丢 tid。
        results = await asyncio.gather(*submit_jobs, return_exceptions=True)
        errors = [r for r in results if isinstance(r, BaseException)]
        if errors:
            raise errors[0]
        for tname, tid in results:
            new_tids[tname] = tid
    for task_name, tid, _, _ in to_poll:
        new_tids[task_name] = tid
    # error 重提的新 tid 即时记入 history（旧 tid 仍保留在 history 供追溯）
    by_name = {tname: (qasm, shots) for tname, qasm, shots in full_specs}

    def _factory(task_name):
        async def _cb(new_tid):
            async with _manifest_lock:
                e = tasks.get(task_name, {})
                hist = list(e.get("history", []))
                if not hist or hist[-1] != new_tid:
                    hist.append(new_tid)
                tasks[task_name] = {
                    "tid": new_tid, "history": hist,
                    "status": "submitted",
                    "shots": by_name[task_name][1], "counts": None,
                }
            await _save()

        return _cb

    ordered = [(tname, new_tids[tname]) for tname, _, _ in full_specs
               if tname not in cached]
    specs_for_poll = [(tname, *by_name[tname]) for tname, _ in ordered]
    tids_for_poll = [tid for _, tid in ordered]

    # 已有 done 的不参与轮询；其余一次 gather 收齐，每收一个即记 done。
    got = {}

    async def _poll_record(tid, spec):
        task_name, qasm_str, shots = spec
        async with _poll_sem:
            counts = await await_quark(
                _tmgr, tid, opts, _submit_sem, qasm_str, shots, task_name,
                attempt_tag=name, on_resubmit=_factory(task_name),
            )
        async with _manifest_lock:
            e = tasks.get(task_name, {})
            tasks[task_name] = {
                "tid": e.get("tid", tid),
                "history": e.get("history", [tid]),
                "status": "done", "shots": shots, "counts": counts,
            }
        await _save()
        return task_name, counts

    if specs_for_poll:
        awaiters = [
            asyncio.create_task(_poll_record(tid, spec))
            for tid, spec in zip(tids_for_poll, specs_for_poll)
        ]
        try:
            for tname, counts in await asyncio.gather(*awaiters):
                got[tname] = counts
        except Exception:
            for a in awaiters:
                a.cancel()
            raise
    got.update(cached)
    return [got[tname] for tname, _, _ in full_specs]


# ── Circuit & params ───────────────────────────────────────────────


def add_meas(qc, params, meas_indices):
    """按分组加随机测量旋转门与测量指令; 所需经典位由 meas 元素个数决定, 自动补齐。"""
    flat_indices = []
    for group_idx, group in enumerate(meas_indices):
        for qubit_idx in group:
            flat_indices.append(qubit_idx)
    if (missing := len(flat_indices) - qc.num_clbits) > 0:
        qc.add_bits([Clbit() for _ in range(missing)])
    theta, phi = params
    for group_idx, group in enumerate(meas_indices):
        for qubit_idx in group:
            qc.u(-theta[group_idx], 0, -phi[group_idx], qubit_idx)
    qc.measure(flat_indices, range(len(flat_indices)))
    return qc


def _to_qasm2(qc, num_settings, binds, opts):
    """绑参数 → 本地 transpile → 防裸测量比特 → QASM2。"""
    qasm_ls = []
    for i in range(num_settings):
        bound = qc.assign_parameters({p: vals[i] for p, vals in binds.items()})
        basic = transpile(
            bound,
            basis_gates=opts.basis_gates,
            optimization_level=opts.optimization_level,
            coupling_map=opts.coupling_map,
        )
        basic = _guard_empty_qubits(basic)
        qasm_ls.append(qasm2.dumps(basic))
    return qasm_ls


def _guard_empty_qubits(qc):
    """Quark 平台缺陷 workaround: 补齐空比特。

    两类空比特平台都会报错：
    1. “裸测量比特”：有测量、没门（transpile 高优化会删掉恒等门，
       如 Z 基 (θ=φ=0) 的 u(0, 0, 0)）→ 在测量前原地插入 rz(0)；
    2. “纯空比特”：没门、连测量都没有（如 XXZ pidx=-1 开链的首尾比特，
       测量只覆盖子集时留下）→ 平台按实际使用比特数匹配 target_qubit，
       少了就报 Target qubits number mismatch → 文末追加 rz(0)。
    rz(0) 是允许的基底门、严格物理恒等，不改变任何测量统计；
    置于 transpile 之后插入，避免被优化掉。
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
    touched = gated | guarded
    for i in range(qc.num_qubits):
        if i not in touched:
            qc.data.append(CircuitInstruction(RZGate(0.0), (qc.qubits[i],), ()))
            touched.add(i)
    return qc


def _prepare_trivial_qc(config, extra_y_qubits=()):
    """开启 mitigation 时构造 |0⟩^⊗n 的 trivial 测量电路, 否则返回 None。

    trivial 电路 = 无态制备 + 与主电路相同的测量旋转与测量指令,
    角度绑定与 shot 数均直接复用主电路的设置。
    extra_y_qubits 非空时先加 σ^y 门（配对实验一的 u_T），
    使标定与主电路的门结构完全一致。
    """
    if not config.runner_opts.mitigation:
        return None

    trivial_qc = QuantumCircuit(config.qc.num_qubits)
    for q in extra_y_qubits:
        trivial_qc.y(q)
    return add_meas(
        trivial_qc,
        config.params,
        config.meas_indices,
    )
