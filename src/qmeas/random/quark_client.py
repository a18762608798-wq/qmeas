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
    "quark_token",
    "make_quark_task",
    "backoff",
    "looks_retryable_submit",
    "submit_quark",
    "fetch_quark_result",
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


async def await_quark(tmgr, tid, opts, submit_sem, qasm_str, shots, name, attempt_tag=""):
    """轮询单个电路结果，返回计数字典（C 策略）。

    - 有 "count"：成功返回。
    - 传输层失败（非 JSON/连接错）：指数退避重试本 tid 查询（poll_retries），
      不建新任务；仍受 poll_timeout 约束。
    - 平台 "error" 非空（含过载误报的 Transpiler 错误）：旧 tid 尽力
      cancel/delete，用原 qasm_str 重提新 tid 再轮询，最多 max_resubmits 次；
      耗尽后抛 RuntimeError 中断整组（C 策略 fail-fast 部分）。
    """
    start = time.monotonic()
    tried_tids = [tid]
    resubmits = 0
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
            continue
        if "count" in res:
            return res["count"]
        err = res.get("error")
        if err:
            if resubmits >= opts.max_resubmits:
                raise RuntimeError(
                    f"quark 任务失败 {name}（tag={attempt_tag}，tids={tried_tids}）: {err!r}。"
                    f"已用原 QASM 重提 {resubmits} 次仍失败；小并发可过而大批量失败多为"
                    f"平台过载误报，建议降 max_submit_concurrency 后补跑该组。"
                )
            resubmits += 1
            print(
                f"quark 任务 {tid}（{name}）平台 error，重提新 tid"
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
            continue


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
