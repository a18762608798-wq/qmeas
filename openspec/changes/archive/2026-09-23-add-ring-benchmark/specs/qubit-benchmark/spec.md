# Spec Delta

## ADDED Requirements

### Requirement: 环形基准约束

环形基准只支持偶数长度；奇数长度 SHALL 直接拒绝而不提交任务。环形候选只在同一次 `recommend()` 内互相比较，SHALL 不与链形候选混排；候选不足填不满 `max_chains_per_chip` 时有多少测多少。

#### Scenario: 奇数环被拒绝

- **WHEN** 用户请求长度为奇数的 ring 基准
- **THEN** 系统明确报错，不产生任何云端调用

#### Scenario: 环链不混排

- **WHEN** 同一配置下既有环候选又有链候选
- **THEN** 系统分开两次推荐，绝不出现在同一排序表中

#### Scenario: 环候选不足

- **WHEN** 可用环数量少于 `max_chains_per_chip`
- **THEN** 系统测量全部可用环，不报错不补凑

## MODIFIED Requirements

### Requirement: 候选链生成与硬过滤

系统 SHALL 以 `priority_qubits` 中对应链长的推荐链为种子，在可用耦合边（`couplers_info` 中保真度大于 0 的边）构成的图上按配置的 `expansion` 策略生成不超过上限的候选链；凡经过死比特（T1/T2/门保真度全零）或零保真边的链 SHALL 被丢弃。`expansion` 取值 `conservative`、`slide`、`multi-seed`、`sample` 之一，默认 `conservative`；`sample` 的静态边保真度预排序只决定采样配额，不参与最终链排序。`shape` 为 `ring` 时，候选为首尾边亦可用的等长简单环，`priority_qubits` 不做 ring 种子，候选取自图搜索。

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

#### Scenario: 环候选来自图搜索

- **WHEN** `shape` 为 `ring` 且请求 10 比特环
- **THEN** 系统返回首尾边可用的 10 环列表（如 Baihua 4 个、Shenglian 9 个量级），不使用 `priority_qubits`

### Requirement: 主动基准测量

系统 SHALL 对每条候选链/环提交 4 个真机测量电路：2 组 cluster 态稳定子测量设置（奇偶分组，各稳定子理论值 +1）与 all-0 / all-1 读出校验电路；默认每电路 1024 shots；提交 SHALL 复用现有 Quark 任务管道的限流、重试与轮询语义；每份电路结果返回后 SHALL 立即落盘，中断后续跑 SHALL 跳过已有结果、只补缺失电路。环形制备为 `H + n CZ`（含闭环边），`coupling_map` 含闭环边。

#### Scenario: 单链完整测量

- **WHEN** 对一条 8 比特候选链执行基准
- **THEN** 系统提交恰好 4 个任务并返回 4 份计数字典

#### Scenario: 单环完整测量

- **WHEN** 对一个 10 比特候选环执行基准
- **THEN** 系统提交恰好 4 个任务并返回 4 份计数字典

#### Scenario: 任务失败中断

- **WHEN** 任一基准任务在重试与重提耗尽后仍失败
- **THEN** 系统抛错并保留已拿到的部分结果与任务 id 供补跑

#### Scenario: 断点续跑

- **WHEN** 进程在部分电路结果已落盘后中断，之后重新执行同一次基准
- **THEN** 系统跳过已有结果的电路，只提交缺失部分，最终合并为完整结果集

### Requirement: 打分排序与推荐

系统 SHALL 按 `chain_score = 0.8 * mean(<S_i>) + 0.2 * readout_fid` 打分，其中 `mean(<S_i>)` 为全部稳定子期望之均值（由计数字典恢复），`readout_fid` 为 all-0/all-1 实测读出保真度；同一次推荐内的全部候选（同形状，跨 Baihua 与 Shenglian）按分数从高到低排序，`recommend()` SHALL 返回第一名及其依据；排队深度 SHALL 只记录不参与排序。

#### Scenario: 正常推荐

- **WHEN** 两台机各有至少一条候选链完成测量
- **THEN** `recommend()` 返回 `(chip, target_qubits, evidence)`，`evidence` 含每条链的稳定子均值、读出保真度、任务 id、快照校准时间与当时排队深度

#### Scenario: 无排队惩罚

- **WHEN** 某台机排队很长但其链实测分最高
- **THEN** 推荐仍指向该机器，不因排队扣分

#### Scenario: 全失败无推荐

- **WHEN** 所有候选链均无有效测量结果
- **THEN** `recommend()` 明确报错而不返回猜测值
