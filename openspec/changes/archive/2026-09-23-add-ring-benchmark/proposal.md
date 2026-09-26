# Proposal: add-ring-benchmark

## Why

部分任务需要环形比特布局（如 10 比特环），现有 `qubit-benchmark` 只支持链形。实测枚举表明两台机上可用 10 环稀少但存在（Baihua 4 个、Shenglian 9 个，去重后），值得用同一套主动基准方法挑出最优环。

## What Changes

- `BenchmarkConfig` 新增 `shape: chain | ring`（默认 `chain`，现有行为零变化）。
- ring 模式：候选为可用边图上的等长简单环（首尾边保真度 > 0），只支持偶数长度，奇数直接拒绝；`priority_qubits`（链式）不做 ring 种子，候选取自图搜索（随机游走首尾相接）。
- ring 基准电路：制备为 `H + n CZ`（含闭环边），稳定子仍按奇偶分 2 组（偶环二分图），4 电路结构、打分公式、checkpoint、推荐输出全复用。
- ring 自成一次 `recommend()`，只内部比较，不与 chain 混排（CZ 数不同，无公平基准）。
- Dongling 依然不测；`max_chains_per_chip` 填不满时有多少测多少（已有“≤上限”语义）。

## Capabilities

### New Capabilities

（无，全部落在现有 capability 的行为扩展。）

### Modified Capabilities

- `qubit-benchmark`：候选拓扑从链扩展到环（形状参数、环候选搜索、偶数约束、环电路制备、不混排规则）。

## Impact

- `src/qmeas/benchmark/`：`config.py` 加 `shape` 字段；`topology.py` 加环枚举；`circuits.py` 加 `ring` 分支；`runner.py` 透传形状；`io.py` 不变。
- 现有 chain 路径行为不变（默认 `shape="chain"`）。
- 首轮 ring 实测预算：Baihua 至多 4 环、Shenglian 至多 9 环 × 4 电路 × 1024 shots（实际按 `max_chains_per_chip` 封顶）。
