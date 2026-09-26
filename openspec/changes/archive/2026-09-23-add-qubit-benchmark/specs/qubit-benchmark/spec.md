# Spec Delta

## Purpose

为主机上的通用量子任务挑选真机与比特链：对 Baihua 与 Shenglian 上的候选比特链做主动基准测量，按实测质量排序并推荐最优（机器，比特）组合，其输出可直接作为后续测量的目标比特映射。

## ADDED Requirements

### Requirement: 拓扑拉取与缓存

系统 SHALL 从云端拉取指定芯片的拓扑与校准快照，并在本地缓存，相同芯片与校准时间不再重复拉取；拉取调用 SHALL 内建节流，连续两次拉取间隔不得小于 60 秒。

#### Scenario: 首次拉取并缓存

- **WHEN** 用户请求 Baihua 的拓扑且本地无该校准时间的缓存
- **THEN** 系统调用一次云端接口并将含 `qubits_info`、`couplers_info`、`priority_qubits`、`calibration_time` 的快照落盘

#### Scenario: 缓存命中不再拉取

- **WHEN** 用户再次请求同一芯片且校准时间未变
- **THEN** 系统直接读本地缓存，不产生新的云端调用

#### Scenario: 节流保护

- **WHEN** 距上次拉取不足 60 秒又有新的拉取请求
- **THEN** 系统拒绝并发起新的调用，返回缓存或明确报错

### Requirement: 候选链生成与硬过滤

系统 SHALL 以 `priority_qubits` 中对应链长的推荐链为种子，在可用耦合边（`couplers_info` 中保真度大于 0 的边）构成的图上按配置的 `expansion` 策略生成不超过上限的候选链；凡经过死比特（T1/T2/门保真度全零）或零保真边的链 SHALL 被丢弃。`expansion` 取值 `conservative`、`slide`、`multi-seed`、`sample` 之一，默认 `conservative`；`sample` 的静态边保真度预排序只决定采样配额，不参与最终链排序。

#### Scenario: 种子链可用时直接采用

- **WHEN** `priority_qubits` 中存在链长 8 的推荐链且其全部边可用
- **THEN** 该链进入候选集，无需扩展

#### Scenario: 死比特与坏边过滤

- **WHEN** 某条候选链经过死比特或零保真边
- **THEN** 系统丢弃该链并在报告中记录丢弃原因

#### Scenario: 候选数量封顶

- **WHEN** 扩展产生的候选链超过配置上限（默认每台机 3 条）
- **THEN** 系统只保留前 N 条并记录截断

#### Scenario: 扩展策略可选

- **WHEN** 用户指定 `expansion` 为 `slide`、`multi-seed` 或 `sample`
- **THEN** 系统按对应策略生成候选链，仍受数量上限与硬过滤约束

### Requirement: 主动基准测量

系统 SHALL 对每条候选链提交 4 个真机测量电路：2 组 cluster 态稳定子测量设置（奇偶分组，各稳定子理论值 +1）与 all-0 / all-1 读出校验电路；默认每电路 1024 shots；提交 SHALL 复用现有 Quark 任务管道的限流、重试与轮询语义；每份电路结果返回后 SHALL 立即落盘，中断后续跑 SHALL 跳过已有结果、只补缺失电路。

#### Scenario: 单链完整测量

- **WHEN** 对一条 8 比特候选链执行基准
- **THEN** 系统提交恰好 4 个任务并返回 4 份计数字典

#### Scenario: 任务失败中断

- **WHEN** 任一基准任务在重试与重提耗尽后仍失败
- **THEN** 系统抛错并保留已拿到的部分结果与任务 id 供补跑

#### Scenario: 断点续跑

- **WHEN** 进程在部分电路结果已落盘后中断，之后重新执行同一次基准
- **THEN** 系统跳过已有结果的电路，只提交缺失部分，最终合并为完整结果集

### Requirement: 打分排序与推荐

系统 SHALL 按 `chain_score = 0.8 * mean(<S_i>) + 0.2 * readout_fid` 打分，其中 `mean(<S_i>)` 为 8 个稳定子期望之均值（由计数字典恢复），`readout_fid` 为 all-0/all-1 实测读出保真度；跨 Baihua 与 Shenglian 的全部候选链按分数从高到低排序，`recommend()` SHALL 返回第一名及其依据；排队深度 SHALL 只记录不参与排序。

#### Scenario: 正常推荐

- **WHEN** 两台机各有至少一条候选链完成测量
- **THEN** `recommend()` 返回 `(chip, target_qubits, evidence)`，`evidence` 含每条链的稳定子均值、读出保真度、任务 id、快照校准时间与当时排队深度

#### Scenario: 无排队惩罚

- **WHEN** 某台机排队很长但其链实测分最高
- **THEN** 推荐仍指向该机器，不因排队扣分

#### Scenario: 全失败无推荐

- **WHEN** 所有候选链均无有效测量结果
- **THEN** `recommend()` 明确报错而不返回猜测值

### Requirement: 输出兼容后续测量

`recommend()` 返回的 `target_qubits` SHALL 可直接赋给 `QuarkOptions.target_qubits` 用于后续 `run_random` / estimator 任务；推荐所用的测量设置分组 SHALL 与 `estimator` 的 qubitwise 分组语义一致。

#### Scenario: 端到端衔接

- **WHEN** 用户把推荐的 `target_qubits` 填入一次新的随机测量任务
- **THEN** 该任务无需修改即提交到推荐芯片的推荐比特上执行
