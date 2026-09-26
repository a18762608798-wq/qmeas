# Proposal: add-qubit-benchmark

## Why

跑一般量子任务前，需要回答“用哪台真机、哪几个比特”。静态校准（T1/T2/门保真度中位数）只能反映整机平均，且读出保真度在 Baihua 上根本没有上报；只有上机实测才能给出可比的链质量排序。`qmeas` 现有 `random` / `estimator` 都假设目标比特已选定，缺一个“选机选比特”的前置模块。

## What Changes

- 新增 `qmeas.benchmark` 子包，对外提供库 API：`recommend()` 返回 `(chip, target_qubits, evidence)`，其中 `target_qubits` 可直接喂给 `QuarkOptions.target_qubits`。
- 基准方法：给定链长的线性 cluster 态稳定子均值为主分（理论值全 +1），all-0/all-1 实测读出保真度为辅分；`chain_score = 0.8 * mean(<S_i>) + 0.2 * readout_fid`。
- 候选链来源：`Task.backend()` 的 `priority_qubits` 做种子，在 `couplers_info` 图上局部扩展；死比特（全零指标）与零保真边硬过滤。
- 覆盖机器：仅 Baihua 与 Shenglian。Dongling 经确认是三台中最差，不测。
- 排序纯按实测质量，不设排队惩罚项；`status()` 队列深度只进报告做溯源。
- `Task.backend()` 单次拉取 + 本地缓存（key = chip + calibration_time），内建调用节流。
- 依赖：`CondaPkg.toml` 已加入 `quarkcircuit`、`matplotlib`（`backend()` 拓扑渲染所需）。

## Capabilities

### New Capabilities

- `qubit-benchmark`: 真机比特链质量主动基准与选机选比特推荐。覆盖拓扑拉取/缓存、候选链生成与过滤、cluster 稳定子基准电路提交与回收、打分排序与推荐输出。

### Modified Capabilities

（无。现有 `random` / `estimator` 行为不变，新模块只复用其网络层与分组逻辑。）

## Impact

- 新增 `src/qmeas/benchmark/`（config/topology/scoring/runner/io），`src/qmeas/__init__.py` 导出。
- 复用 `random/quark_client.py`（提交/轮询/重提）与 `estimator/basis.py`（qubitwise 分组与恢复），不改其接口。
- 新增 pip 依赖 `quarkcircuit`、`matplotlib`（已进 `CondaPkg.toml`）。
- 首轮实测预算：每台机至多 3 条候选链 × 4 电路 × 1024 shots，共 ≤24 个真机任务；排队等待（尤其 Baihua queue~25）计入工时。
