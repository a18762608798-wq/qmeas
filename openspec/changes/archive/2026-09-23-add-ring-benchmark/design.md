# Design

## Context

- `qubit-benchmark` 主 spec 已归档（chain-8 全链路实测验证，Baihua 0.716 胜出）。复用：`quark_client` 网络层、`group_qubitwise` + `recover`、checkpoint 续跑、`recommend()`/`save_report`。动机见 proposal.md。
- 已实测枚举（fixture 快照，零保真边与死比特剔除）：Baihua 4 个、Shenglian 9 个可用 10 环（无向去重后）。Baihua `[125…138]` 环包住首轮冠军 8 链，可互证。
- 偶数环的 cluster 稳定子图是二分图，奇偶分组继续成立；奇数环需 3 组，本 change 不做。

## Goals / Non-Goals

**Goals:**

- `BenchmarkConfig.shape: chain | ring`（默认 `chain`，老行为零变化）。
- ring 候选枚举 + ring 电路分支 + 同形状内推荐；`example` 加 ring 用法。
- 奇数 ring 明确拒绝（零云端调用）。

**Non-Goals:**

- 不做奇数环、不做 ring/chain 混排、不测 Dongling。
- 不改 chain 路径的任何行为与已验证分数。

## Decisions

1. **环枚举 = 可用边图上等长简单环 DFS，以最小节点为起点去重（旋转/翻转归一）。** 在 156 节点图上 10 环枚举秒级完成（已验证）；`priority_qubits` 不做 ring 种子。`expansion` 四档在 ring 下的语义：`sample` 改为“游走首尾相接即成环”（天然适配，为 ring 默认推荐）；`conservative`/`slide`/`multi-seed` 为链式思维，ring 下禁用并明确报错，避免静默产出链。
2. **ring 电路 = `circuits.build_circuits(n, ring=True)` 分支。** 制备 `H + n CZ`（多一条闭环边）；稳定子 `S_i = Z_{i-1} X_i Z_{i+1 mod n}` 仍走 `group_qubitwise` 得奇偶 2 组；all-0/all-1 不变。打分、`recover`、`coupling_map`（加闭环边）、`target_qubits`、`transpiled`验 pin 全复用。
3. **奇数拒绝前置。** `build_circuits` 与 `build_chains` 入口即校验偶数，与 `config.chain_length` 的 ≥2 校验并列；早于任何网络调用。
4. **不混排。** `recommend()` 按调用时传入的同形状结果集排序；跨形状比较在 API 层面不提供（传了也各算各的，不合并）。
5. **填不满即有多少测多少。** `max_chains_per_chip` 保持“≤上限”语义；`bill()` 照实际候选数算，不虚增。

## Risks / Trade-offs

- [环候选极少，`sample` 随机性导致批次间名单抖动] → 固定 `rng_seed`（默认 0），报告记录种子以复现。
- [校准漂移导致某次快照零可用环] → 返回空列表 + 明确原因（非报错），`recommend()` 走“全失败无推荐”分支。
- [奇环需求未来出现] → 非本 change；需 3 分组 + 新验证，另立 change。
- [Baihua/Shenglian 拓扑变化] → 枚举每次基于实时快照，不硬编码环名单；fixture 仅供测试。

## Migration Plan

纯加法。默认 `shape="chain"`，老调用零变化；`qmeas.benchmark.__all__` 新增符号（如 `find_rings`）为加法导出。

## Open Questions

- `sample` 在 ring 下的预排序权重（边保真度和 vs 瓶颈边最小值）待首批 ring 实测后看分布再定，不影响结构。
