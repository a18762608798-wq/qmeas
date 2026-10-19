"""Quark 真机 HTTP 管道：限流、重试、轮询、error 重提。

纯网络层，与测量管线编排无关。并发模型（大批量关键）：
- 全 run 共享 submit_sem / poll_sem：只限同一瞬间的 HTTP 并发（速度），
  不限任务总数；提交走 submit_sem，轮询走 poll_sem。
- 全 run 共享同一个 Task（主线程构造一次）：quark Task 是单例且每次构造
  都打 /task/verify，无节流；复用后 verify 从 O(任务数) 降为 O(1)。
- 失败分级：传输层失败（非 JSON/空响应/连接错）→ 指数退避重试本操作，
  不建新任务；平台 error（如过载误报的 Transpiler 错误）→ 旧 tid 尽力
  cancel/delete 后，用原 QASM 重提新 tid（max_resubmits 次，默认 1），
  耗尽后抛错中断（C 策略）。
- 空字典黑洞：空 {} / 非 dict 时每 _EMPTY_STATUS_CHECK_EVERY 轮调
  status(tid) 区分排队中与终态，仅终态（Finished/Failed/Cancelled/Canceled）
  却无 count 才按异常走原 QASM 重提（max_resubmits 次）；
  Submitted/Running 等非终态继续等待，绝不误杀。
"""

import asyncio
import json
import os
import random
import time

import requests
from quark import Task

__all__ = [
    "TRANSIENT_HTTP_ERRORS",
    "RETRYABLE_SUBMIT_KEYWORDS",
    "DETERMINISTIC_ERROR_KEYWORDS",
    "TERMINAL_TASK_STATUSES",
    "quark_token",
    "make_quark_task",
    "backoff",
    "looks_retryable_submit",
    "submit_quark",
    "fetch_quark_result",
    "fetch_quark_status",
    "await_quark",
    "abandon_quark_task",
]

TRANSIENT_HTTP_ERRORS = (
    json.JSONDecodeError,
    requests.exceptions.RequestException,
    ValueError,
)

# submit 返回 dict 而非 int 时，视为可重试的服务端限流/过载信号。
RETRYABLE_SUBMIT_KEYWORDS = (
    "limit", "frequent", "busy", "timeout", "retry", "overload", "queue",
    "429", "502", "503", "504", "empty", "verify",
)

# 平台 error 含这些关键字时视为确定性错误（如比特映射错配），重提必败，
# 直接抛错，不消耗 max_resubmits（省配额）。
DETERMINISTIC_ERROR_KEYWORDS = ("mismatch",)

# 任务终态：这些状态表示任务已结束（无论成败），据此判定"终态空包黑洞"。
TERMINAL_TASK_STATUSES = ("Finished", "Failed", "Cancelled", "Canceled")
# 连续多少次空 {} 才调一次 tmgr.status(tid) 探终态（控请求量；排队数小时不误杀）
_EMPTY_STATUS_CHECK_EVERY = 3


def quark_token(opts) -> str:
    token = opts.token or os.environ.get("QUARK_TOKEN")
    if not token:
        raise RuntimeError("缺少 QUARK_TOKEN：请设置环境变量或 QuarkOptions.token")
    return token


def make_quark_task(token, retries):
    """主线程构造共享 Task；verify 的瞬时失败做指数退避重试。"""
    last = None
    for attempt in range(retries + 1):
        try:
            tmgr = Task(token)
            try:
                tmgr.session.mount(
                    "https://",
                    requests.adapters.HTTPAdapter(
                        pool_connections=16, pool_maxsize=48, max_retries=0
                    ),
                )
            except Exception:
                pass
            return tmgr
        except TRANSIENT_HTTP_ERRORS as e:
            last = e
            time.sleep(backoff(attempt))
    raise RuntimeError(f"quark Task 初始化失败（已重试 {retries} 次）: {last!r}")


def backoff(attempt, base=2.0, cap=120.0):
    return min(cap, base * (2.0**attempt)) + random.uniform(0, 1.0)


def looks_retryable_submit(payload) -> bool:
    s = json.dumps(payload, ensure_ascii=False, default=str).lower()
    return any(k in s for k in RETRYABLE_SUBMIT_KEYWORDS)


def submit_quark(tmgr, opts, qasm_str, shots, name):
    """同步阻塞提交（调用方负责 to_thread + submit_sem）；传输层失败退避重试。

    成功返回 int tid；服务端明确拒收（非限流类 dict）直接抛错。
    """
    task = {
        "chip": opts.chip,
        "shots": shots,
        "name": name,
        "circuit": qasm_str,
        "options": {
            "compiler": "qiskit",
            "correct": opts.correct,
            "target_qubits": opts.target_qubits,
        },
    }
    last = None
    for attempt in range(opts.submit_retries + 1):
        try:
            tid = tmgr.run(task)
        except TRANSIENT_HTTP_ERRORS as e:
            last = e
            time.sleep(backoff(attempt))
            continue
        if isinstance(tid, int):
            return tid
        # 非 int：平台回了 dict payload，限流类则重试，否则抛错
        last = tid
        if looks_retryable_submit(tid):
            time.sleep(backoff(attempt))
            continue
        raise RuntimeError(f"quark 提交被拒 {name}: {tid!r}")
    raise RuntimeError(f"quark 提交失败 {name}（已重试 {opts.submit_retries} 次）: {last!r}")


def fetch_quark_result(tmgr, tid):
    """同步阻塞取结果（调用方负责 to_thread）；只取一次，重试由外层负责。"""
    return tmgr.result(tid)


def _extract_status(st, tid=None):
    """从 tmgr.status(tid) 的各种返回形态提取状态字符串；提取不到返回 None。"""
    if isinstance(st, str):
        return st.strip()
    if isinstance(st, dict):
        if isinstance(st.get("status"), str):
            return st["status"].strip()
        if tid is not None:
            for key in (tid, str(tid)):
                if key in st:
                    return _extract_status(st[key], tid)
    return None


def fetch_quark_status(tmgr, tid):
    """同步阻塞查单任务状态（调用方负责 to_thread）；只取一次，异常由外层负责。"""
    return tmgr.status(tid)


async def await_quark(tmgr, tid, opts, submit_sem, qasm_str, shots, name, attempt_tag="", on_resubmit=None):
    """轮询单个电路结果，返回计数字典（C 策略）。

    - 有 "count"：成功返回。
    - 空 dict {} / 非 dict：每 _EMPTY_STATUS_CHECK_EVERY 轮调 tmgr.status(tid)
      区分排队中与终态；仅终态（Finished/Failed/Cancelled/Canceled）却无
      "count" 才视同 error 重提新 tid，Submitted/Running/Pending 等非终态或
      状态查询失败/未知一律继续等待（排队数小时不误杀）。
    - 非空 dict 为终态（status ∈ TERMINAL_TASK_STATUSES）却无 "count"：
      视同 error 重提新 tid；未完成中间态（Running/Pending/Compiling
      或无 status）正常继续等。
    - 传输层失败（非 JSON/连接错）：指数退避重试本 tid 查询（poll_retries），
      不建新任务；仍受 poll_timeout 约束。
    - 平台 "error" 非空（含过载误报的 Transpiler 错误）：旧 tid 尽力
      cancel/delete，用原 qasm_str 重提新 tid 再轮询，最多 max_resubmits 次；
      耗尽后抛 RuntimeError 中断整组（C 策略 fail-fast 部分）。
      含 DETERMINISTIC_ERROR_KEYWORDS（如 mismatch）的确定性错误不重提，
      直接抛错（重提必败，省配额）。
    - on_resubmit 非空时，每次重提成功后调用 ``await on_resubmit(new_tid)``，
      供调用方把新 tid 记入 manifest（断点续跑用）。
    """
    start = time.monotonic()
    tried_tids = [tid]
    resubmits = 0
    empty_rounds = 0

    async def _resubmit(err):
        """异常返回/error 统一重提：确定性检查 → 配额检查 → 放弃旧 tid → 提新 tid。"""
        nonlocal tid, resubmits, empty_rounds
        if any(k in str(err).lower() for k in DETERMINISTIC_ERROR_KEYWORDS):
            raise RuntimeError(
                f"quark 任务失败 {name}（tag={attempt_tag}，tids={tried_tids}）: {err!r}。"
                f"此类错误是确定性的（如电路比特数与 target_qubits 不匹配），"
                f"重提必败，故不重提直接中断；请检查电路/映射后补跑该组。"
            )
        if resubmits >= opts.max_resubmits:
            raise RuntimeError(
                f"quark 任务失败 {name}（tag={attempt_tag}，tids={tried_tids}）: {err!r}。"
                f"已用原 QASM 重提 {resubmits} 次仍失败；小并发可过而大批量失败多为"
                f"平台过载误报，建议降 max_submit_concurrency 后补跑该组。"
            )
        resubmits += 1
        empty_rounds = 0
        print(
            f"quark 任务 {tid}（{name}）异常，重提新 tid"
            f"（第 {resubmits}/{opts.max_resubmits} 次）: {err!r}"
        )
        try:
            await asyncio.to_thread(abandon_quark_task, tmgr, tid)
        except Exception:
            pass
        await asyncio.sleep(backoff(resubmits, base=15.0, cap=180.0))
        async with submit_sem:
            tid = await asyncio.to_thread(
                submit_quark, tmgr, opts, qasm_str, shots, name
            )
        tried_tids.append(tid)
        if on_resubmit is not None:
            await on_resubmit(tid)

    # 首轮抖动，打散全 run 整齐轮询的惊群
    await asyncio.sleep(random.uniform(0, min(opts.poll_interval, 5.0)))
    while True:
        if opts.poll_timeout is not None and time.monotonic() - start > opts.poll_timeout:
            raise TimeoutError(
                f"quark 轮询超时 {name}（tag={attempt_tag}，tids={tried_tids}，"
                f"timeout={opts.poll_timeout}s）"
            )
        await asyncio.sleep(opts.poll_interval * random.uniform(0.8, 1.2))
        try:
            res = await asyncio.to_thread(fetch_quark_result, tmgr, tid)
        except TRANSIENT_HTTP_ERRORS as e:
            # 瞬时网络失败：有限次快速退避后继续外层循环（仍受总超时约束）
            ok = False
            for attempt in range(opts.poll_retries):
                await asyncio.sleep(backoff(attempt, base=1.0, cap=30.0))
                try:
                    res = await asyncio.to_thread(fetch_quark_result, tmgr, tid)
                    ok = True
                    break
                except TRANSIENT_HTTP_ERRORS:
                    continue
            if not ok:
                print(f"quark 任务 {tid}（{name}）查询抖动，继续等待：{e!r}")
                continue
        if not isinstance(res, dict) or not res:
            empty_rounds += 1
            if empty_rounds % _EMPTY_STATUS_CHECK_EVERY != 0:
                continue
            # 每 N 轮空包探一次状态：只有确认终态才重提，排队中绝不误杀
            try:
                raw_st = await asyncio.to_thread(fetch_quark_status, tmgr, tid)
            except Exception:
                continue
            st = _extract_status(raw_st, tid)
            if st in TERMINAL_TASK_STATUSES:
                await _resubmit(f"empty result dict while status={st!r}")
            continue
        empty_rounds = 0
        if "count" in res:
            return res["count"]
        err = res.get("error")
        if err:
            await _resubmit(err)
            continue
        if res.get("status") in TERMINAL_TASK_STATUSES:
            await _resubmit(f"terminal status {res.get('status')!r} without count")
            continue
        # 未完成中间态（Submitted/Running/Pending/Compiling 或无 status）：继续等


def abandon_quark_task(tmgr, tid):
    """尽力 cancel + delete 旧 tid；失败一律吞掉（重提流程不受影响）。"""
    try:
        tmgr.cancel(tid)
    except Exception:
        pass
    try:
        tmgr.delete(tid)
    except Exception:
        pass
