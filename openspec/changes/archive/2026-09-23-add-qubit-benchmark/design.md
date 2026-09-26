# Design

## Context

- `qmeas` 现有 `random/quark_client.py`（共享 Task 单例、提交/轮询限流、指数退避、error 重提）与 `runner.py` 的 `_guard_empty_qubits`、`_to_qasm2`；`estimator/basis.py` 有 `group_qubitwise`、`QubitwiseBasis.recover`、`PAULI_ROTATIONS`。新模块只复用，不改接口。动机见 proposal.md。
- 已实测确认：`Task.backend(chip)` 返回结构化 dict（`qubits_info` 84/156 项、`couplers_info`、`priority_qubits`、`global_info`、`calibration_time`）；`fidelity == 0` 的边不可用、全零指标的比特为死比特；Baihua 读出保真度全 0.0。`backend()` 在 headless 环境会挂起，根因是 `bk.draw()` 内调 `show()` 被默认交互后端阻塞（已实证，与服务端限流无关）；`topology.py` 必须在 `import quark` 前先 `matplotlib.use('Agg')`（实测约 10 秒返回），节流 + 缓存保留作 courtesy。
- 环境已就绪：`CondaPkg.toml` 有 `quarkcircuit`、`matplotlib`。
- `Task()` 无参读的是 `QPU_API_TOKEN` 环境变量而非 `QUARK_TOKEN`；`qmeas` 经 `quark_token()` 显式传参，不受影响，备查。

## Goals / Non-Goals

**Goals:**

- `qmeas.benchmark` 子包：`config.py`、`topology.py`、`scoring.py`、`circuits.py`、`runner.py`、`io.py`，与现有 `random` / `estimator` 同构。
- 每条候选链恰好 4 个电路、默认 1024 shots，首轮 ≤24 个真机任务。
- 推荐输出直接兼容 `QuarkOptions.target_qubits`。

**Non-Goals:**

- 不做 Dongling（确认最差，不测）。
- 不做通用 RB / mirror / GHZ 等第二基准；不做误差缓解（mitigation）版本。
- 不自动触发后续测量任务，只返回推荐。

## Decisions

1. **候选链 = `priority_qubits` 种子 + `expansion` 策略，上限 `max_chains_per_chip`。** 官方推荐链已验证为连通链（Baihua `[128, 129, 142, ...]` 逐段对过 `couplers_info`），做种子比全图搜链便宜。四档策略由保守到激进：
    - `conservative`（默认）：种子邻域一跳替换，一次只换一个位置。
    - `slide`：种子沿图向外延一格、从另一端截一格，等长滑动，每方向可滑多步。
    - `multi-seed`：`priority_qubits` 里邻近链长（7/9/10）的推荐链截长/补短成目标链长，各成独立种子，再各做一跳。
    - `sample`：可用边图上分区域随机游走采样，去重后按静态边保真度和预排序取前 N；静态分只定采样配额，不进最终排序（守住纯主动立场）。
    备选（无种子时全图 beam search）保留为回退：从最高保真边贪心走（见 Open Questions）。
2. **4 电路结构：2 组稳定子 + all-0 + all-1。** 8 链 cluster 稳定子 `S_i = X_i Z_{i-1} Z_{i+1}` 按奇偶分成 2 个 qubit-wise 对易组，直接调 `estimator` 的 `group_qubitwise` + `recover`；制备仅 `H + 7 CZ`（CZ 是云端原生两比特门，`global_info.two_qubit_gate_basis == 'CZ'`），深度浅，分数主要反映 2Q 门 + 读出。all-0/all-1 把读出单独剥离（Baihua 静态读出全缺，必须实测）。
3. **提交参数：`target_qubits=候选链`，`coupling_map=链边`，其余沿用 `QuarkOptions` 默认（含 `basis_gates=["rz","rx","ry","cz"]`、optimization_level=3、`_guard_empty_qubits`）。** coupling_map pin 住线性拓扑，防止本地 transpile 引入非链边两比特门污染基准。
4. **`topology.py` 单次拉取 + json 落盘（key=chip+calibration_time）+ 60 秒节流，并强制 Agg 后端（`import quark` 之前先 `matplotlib.use('Agg')`，否则无头服务器上 `bk.draw()` 的 `show()` 永久阻塞）。** 针对已查明的 headless 挂起根因；SVG（`save_svg_fname`）只在用户要求人眼核对时存，不做机器解析。
5. **打分 `0.8 * mean(<S_i>) + 0.2 * readout_fid`，排序取 max，不设排队惩罚。** 权重待首批实测数据后校准（见 Open Questions）；`status()` 队列只写进 evidence。
6. **runner 复用 `quark_client.await_quark` 整套语义**（C 策略 fail-fast + 重提），两台机的链分开跑、各自 `asyncio.gather`，避免一台机的排队拖住另一台。async 只管并发提交/轮询速度；防丢数靠 checkpoint：每个 await 完成即写 partial 结果（沿 `random/io.py` 的落盘风格，文件名带 chain/task 标识，原子写防半截文件），两机各自的 checkpoint 文件；resume 时先扫 checkpoint，只提交缺失电路。

## Risks / Trade-offs

- [`backend()` headless 挂起，根因已查明为 matplotlib 交互后端阻塞] → `import quark` 前强制 Agg + 单次拉取 + 缓存 + 60s 节流；联调前先 `status()` 看队列。
- [Baihua queue~25，真机等待不可控] → `poll_timeout` 保持 24h 默认；首轮小预算验证链路。
- [`target_qubits` 是否真按映射执行尚未验证] → 首轮任务后对照云端返回/编译信息确认；若为自动布局，推荐退化为“选机”而非“选比特”，需回炉设计。
- [静态校准漂移] → evidence 强制带 `calibration_time` 与测量时间戳；两者差过大时报告告警。
- [Baihua 读出静态全缺] → 已由 all-0/all-1 主动测量覆盖，非风险，记录在此备查。
- [cluster 分数与真实任务表现脱节] → 首轮后对比 shadow 任务的实际 SEM；脱节则加 mirror 第二维（非本 change）。

## Migration Plan

纯新增，无迁移、无回滚需求。`qmeas/__init__.py` 加 `benchmark` 导出；`README.md` 补模块说明。

## Open Questions

- `w_stab:w_ro = 0.8:0.2` 为初值，首批实测数据出来后校准（不改变 spec 结构，只调参）。
- `priority_qubits` 在未来校准中若缺失某链长，回退到纯图搜的最短实现已在 tasks 留项。
