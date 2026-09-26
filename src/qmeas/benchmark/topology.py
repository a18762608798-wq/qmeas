"""拓扑拉取与缓存：单次 backend() + json 落盘 + 调用节流。

必须在 `import quark` 之前把 matplotlib 切到 Agg，否则 `bk.draw()` 内的
`show()` 在无头环境永久阻塞（已实证）。因此 quark 只在函数内延迟导入。
"""

import json
import time

import matplotlib

matplotlib.use("Agg")

from .config import BenchmarkConfig

__all__ = [
    "fetch_topology",
    "is_dead_qubit",
    "usable_edges",
    "priority_seed",
    "chain_is_valid",
    "ring_is_valid",
    "expand_chains",
    "slide_chains",
    "multi_seeds",
    "sample_chains",
    "find_rings",
    "build_chains",
]

# chip -> 上次实际拉取的 monotonic 时间戳（进程内节流）
_last_fetch: dict[str, float] = {}


def _cache_path(config: BenchmarkConfig, chip: str, calibration_time: str):
    safe_ts = "".join(c if c.isalnum() else "_" for c in calibration_time)
    d = config.output_dir / "topology_cache"
    return d / f"{chip}_{safe_ts}.json"


def _cached_snapshot(config: BenchmarkConfig, chip: str):
    d = config.output_dir / "topology_cache"
    if not d.is_dir():
        return None
    cands = sorted(d.glob(f"{chip}_*.json"))
    if not cands:
        return None
    with cands[-1].open(encoding="utf-8") as f:
        return json.load(f)


def fetch_topology(config: BenchmarkConfig, chip: str, *, force: bool = False):
    """拉取芯片拓扑快照（含 qubits_info / couplers_info / priority_qubits）。

    - 非 force 且本地有缓存：直接读缓存，零网络请求。
    - 距上次实际拉取不足 `fetch_throttle_interval` 且无缓存：抛错，不调用。
    - 否则调一次 `Task.backend(chip)` 并落盘，key 含 calibration_time。
    """
    from quark import Task  # 延迟导入：Agg 必须先就位
    from ..random.quark_client import quark_token

    if not force:
        snap = _cached_snapshot(config, chip)
        if snap is not None:
            return snap
        last = _last_fetch.get(chip)
        if last is not None and time.monotonic() - last < config.fetch_throttle_interval:
            raise RuntimeError(
                f"topology 拉取被节流：距上次拉取 {chip} 不足 "
                f"{config.fetch_throttle_interval}s 且无本地缓存"
            )
    tmgr = Task(quark_token(config.runner_opts))
    info = tmgr.backend(chip)
    if not isinstance(info, dict) or "qubits_info" not in info:
        raise RuntimeError(f"backend({chip}) 未返回拓扑结构: {type(info)}")
    _last_fetch[chip] = time.monotonic()
    path = _cache_path(config, chip, info.get("calibration_time", "unknown"))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False)
    tmp.replace(path)
    return info


def is_dead_qubit(qinfo: dict) -> bool:
    """死比特：T1/T2/门保真度全零（如 Baihua Q66/Q111）。"""
    return not (qinfo.get("T1") or qinfo.get("T2") or qinfo.get("fidelity"))


def usable_edges(info: dict) -> dict[tuple[int, int], float]:
    """可用耦合边：fidelity > 0。返回 {(min, max): fidelity}。"""
    edges = {}
    for c in info.get("couplers_info", {}).values():
        a, b = c["qubits_index"]
        f = c.get("fidelity", 0) or 0
        if f > 0:
            edges[(min(a, b), max(a, b))] = f
    return edges


def priority_seed(info: dict, length: int):
    """取 priority_qubits 中对应链长的推荐链，无则返回 None。"""
    for cand in info.get("priority_qubits", []):
        if len(cand) == length:
            return list(cand)
    return None


def _adjacency(info: dict):
    adj: dict[int, set[int]] = {}
    for (a, b) in usable_edges(info):
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    return adj


def _dead_indices(info: dict) -> set[int]:
    return {
        q.get("index", int(label[1:]))
        for label, q in info.get("qubits_info", {}).items()
        if is_dead_qubit(q)
    }


def chain_is_valid(chain, info: dict) -> bool:
    """硬过滤：无重复节点、无死比特，且相邻两两有可用边。"""
    if not chain or len(set(chain)) != len(chain):
        return False
    dead = _dead_indices(info)
    if any(q in dead for q in chain):
        return False
    edges = usable_edges(info)
    return all(
        (min(a, b), max(a, b)) in edges for a, b in zip(chain, chain[1:])
    )


def expand_chains(seed, info: dict, max_chains: int):
    """种子邻域一跳替换：每次只换一个位置，保持链长与连通性。"""
    adj = _adjacency(info)
    dead = _dead_indices(info)
    out = [list(seed)]
    seen = {tuple(seed)}
    n = len(seed)
    for i in range(n):
        preds = {seed[i - 1]} if i > 0 else set()
        succs = {seed[i + 1]} if i < n - 1 else set()
        anchors = preds | succs
        cands = set()
        for a in anchors:
            cands |= adj.get(a, set())
        for nb in sorted(cands):
            if nb in dead or nb in seed:
                continue
            var = list(seed)
            var[i] = nb
            t = tuple(var)
            if t not in seen and chain_is_valid(var, info):
                seen.add(t)
                out.append(var)
                if len(out) >= max_chains:
                    return out
    return out


def slide_chains(seed, info: dict, max_chains: int):
    """等长滑动：种子沿图向外延一格、从另一端截一格，BFS 由近及远。"""
    adj = _adjacency(info)
    out = [list(seed)]
    seen = {tuple(seed)}
    queue = [list(seed)]
    while queue and len(out) < max_chains:
        cur = queue.pop(0)
        for new_head in sorted(adj.get(cur[0], set())):
            var = [new_head] + cur[:-1]
            t = tuple(var)
            if t not in seen and chain_is_valid(var, info):
                seen.add(t)
                out.append(var)
                queue.append(var)
                if len(out) >= max_chains:
                    return out
        for new_tail in sorted(adj.get(cur[-1], set())):
            var = cur[1:] + [new_tail]
            t = tuple(var)
            if t not in seen and chain_is_valid(var, info):
                seen.add(t)
                out.append(var)
                queue.append(var)
                if len(out) >= max_chains:
                    return out
    return out


def multi_seeds(info: dict, length: int):
    """多种子：邻近链长的推荐链截长/补短成目标链长，只收有效者。"""
    adj = _adjacency(info)
    seeds = []

    def _add(chain):
        t = tuple(chain)
        if len(chain) == length and chain_is_valid(chain, info):
            if t not in {tuple(s) for s in seeds}:
                seeds.append(list(chain))

    _add(priority_seed(info, length) or [])
    for cand in info.get("priority_qubits", []):
        if len(cand) == length:
            continue
        if len(cand) > length:
            for i in range(len(cand) - length + 1):
                _add(cand[i:i + length])
        elif len(cand) == length - 1:
            head, tail = cand[0], cand[-1]
            for nb in sorted(adj.get(head, set())):
                _add([nb] + cand)
            for nb in sorted(adj.get(tail, set())):
                _add(cand + [nb])
    return seeds


def sample_chains(info: dict, length: int, n: int, rng_seed: int = 0):
    """分区域随机游走采样：按坐标四象限轮换起点，去重后按边保真度和取前 N。"""
    import random

    rng = random.Random(rng_seed)
    adj = _adjacency(info)
    edges = usable_edges(info)
    coords = {
        q.get("index", int(label[1:])): tuple(q.get("coordinate", (0, 0)))
        for label, q in info.get("qubits_info", {}).items()
    }
    xs = sorted(c[0] for c in coords.values())
    ys = sorted(c[1] for c in coords.values())
    mx, my = xs[len(xs) // 2], ys[len(ys) // 2]
    regions: dict[tuple[int, int], list[int]] = {}
    for qi, (x, y) in coords.items():
        regions.setdefault((x >= mx, y >= my), []).append(qi)
    starts = [sorted(v) for v in regions.values() if v]

    found: dict[tuple, float] = {}
    attempts = 0
    while len(found) < n * 4 and attempts < n * 200:
        attempts += 1
        region = starts[attempts % len(starts)]
        start = rng.choice(region)
        chain = [start]
        while len(chain) < length:
            nxt = [nb for nb in adj.get(chain[-1], set()) if nb not in chain]
            if not nxt:
                break
            chain.append(rng.choice(sorted(nxt)))
        if len(chain) == length and chain_is_valid(chain, info):
            t = tuple(chain)
            if t not in found:
                found[t] = sum(
                    edges[(min(a, b), max(a, b))]
                    for a, b in zip(chain, chain[1:])
                )
    ranked = sorted(found, key=lambda t: found[t], reverse=True)
    return [list(t) for t in ranked[:n]]


def ring_is_valid(ring, info: dict) -> bool:
    """环硬过滤：无重复节点、无死比特，相邻（含首尾）两两有可用边。"""
    if not ring or len(set(ring)) != len(ring):
        return False
    dead = _dead_indices(info)
    if any(q in dead for q in ring):
        return False
    edges = usable_edges(info)
    pairs = list(zip(ring, ring[1:])) + [(ring[-1], ring[0])]
    return all((min(a, b), max(a, b)) in edges for a, b in pairs)


def find_rings(info: dict, length: int):
    """枚举等长简单环：DFS + 最小节点起点去重，旋转/翻转归一。

    只支持偶数长度；`priority_qubits` 不做 ring 种子。
    返回去重后的环列表（每环首元为最小节点，正反向取字典序小者）。
    """
    if length % 2:
        raise ValueError(f"ring 只支持偶数长度，当前 {length}")
    adj = _adjacency(info)
    found: set[tuple] = set()
    for s in sorted(adj):
        stack = [[s]]
        while stack:
            path = stack.pop()
            if len(path) == length:
                if path[0] in adj[path[-1]]:
                    rev = (path[0],) + tuple(reversed(path[1:]))
                    found.add(min(tuple(path), rev))
                continue
            for nb in sorted(adj[path[-1]]):
                if nb >= s and nb not in path:
                    stack.append(path + [nb])
    return [
        list(c) for c in sorted(found)
        if ring_is_valid(list(c), info)
    ]


def build_chains(info: dict, length: int, max_chains: int,
                 expansion: str = "conservative", shape: str = "chain"):
    """候选链/环：chain 走 priority 种子 + expansion 策略；ring 走全图环枚举。

    ring 下 expansion 仅 sample 有效（其余三档为链式思维，明确报错）；
    奇数长度在网络调用前拒绝。返回 (chains, dropped)。
    """
    if shape == "ring":
        if length % 2:
            raise ValueError(f"ring 只支持偶数长度，当前 {length}")
        if expansion != "sample":
            raise ValueError(
                f"ring 下 expansion 仅支持 sample，当前 {expansion!r}"
            )
        rings = find_rings(info, length)
        edges = usable_edges(info)
        ranked = sorted(
            rings,
            key=lambda c: sum(
                edges[(min(a, b), max(a, b))] for a, b in
                list(zip(c, c[1:])) + [(c[-1], c[0])]
            ),
            reverse=True,
        )
        return ranked[:max_chains], []
    dropped = []
    if expansion == "multi-seed":
        seeds = multi_seeds(info, length)
    else:
        seed = priority_seed(info, length)
        seeds = []
        if seed is not None:
            if chain_is_valid(seed, info):
                seeds = [seed]
            else:
                dropped.append((seed, "seed 经过死比特、重复节点或零保真边"))
    chains: list[list[int]] = []
    if expansion == "sample":
        if seeds:
            chains.append(seeds[0])
        chains += [
            c for c in sample_chains(info, length, max_chains) if c not in chains
        ]
    else:
        for seed in seeds:
            if expansion == "slide":
                ext = slide_chains(seed, info, max_chains)
            else:  # conservative / multi-seed：一跳替换
                ext = expand_chains(seed, info, max_chains)
            for c in ext:
                if c not in chains:
                    chains.append(c)
                if len(chains) >= max_chains:
                    break
            if len(chains) >= max_chains:
                break
    if chains:
        return chains[:max_chains], dropped
    # 回退：从最高保真边贪心走出一条链
    edges = usable_edges(info)
    dead = _dead_indices(info)
    if not edges:
        return [], dropped
    (a, b), _ = max(edges.items(), key=lambda kv: kv[1])
    chain = [a, b]
    adj = _adjacency(info)
    while len(chain) < length:
        nxt = [
            nb
            for nb in sorted(adj.get(chain[-1], set()))
            if nb not in dead and nb not in chain
        ]
        if not nxt:
            break
        # 选与已走边保真度和最高者
        best = max(
            nxt,
            key=lambda nb: edges.get(
                (min(chain[-1], nb), max(chain[-1], nb)), 0
            ),
        )
        chain.append(best)
    if len(chain) == length and chain_is_valid(chain, info):
        return [chain], dropped
    dropped.append((chain, "贪心回退未能走出满长有效链"))
    return [], dropped
