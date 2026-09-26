# Tasks

## 1. 配置与环枚举

- [x] 1.1 `config.py` 加 `shape` 字段（`chain`/`ring`，默认 `chain`，非法值抛错），以非法值抛错与默认值验证
- [x] 1.2 `topology.py` 加环枚举（等长简单环 DFS + 旋转/翻转去重，`priority_qubits` 不做种子），以 fixture 上 Baihua 枚举出 4 个、Shenglian 9 个 10 环验证
- [x] 1.3 `build_chains` 接 `shape` 分发：ring 下 `expansion` 仅 `sample` 有效（其余三档明确报错），奇数长度在网络调用前拒绝，以非法组合报错与偶数 10 环正常产出验证

## 2. 环电路与打分复用

- [x] 2.1 `circuits.py` 加 `ring` 分支（制备 `H + n CZ` 含闭环边，稳定子模 n 下标，仍奇偶 2 组），以 Aer 无噪声下 10 环稳定子均值≈1 验证
- [x] 2.2 打分链路复用确认（`score_chain`/`recommend` 不变，仅跑通），以模拟的两环分数输入返回正确第一名且不与链混排验证

## 3. 运行器与示例

- [x] 3.1 `runner.py` 透传 `shape`（`coupling_map` 含闭环边，`target_qubits` 为环顺序），以 mock `Task` 离线验证任务组装与 `transpiled` 闭环边一致
- [x] 3.2 `example/benchmark` 加 ring 用法（`shape="ring"` 显式注释 + 偶数约束说明），以 py_compile 通过验证（不实际提交）
- [x] 3.3 `tests/test-benchmark` 补 ring 测试（枚举数、奇数拒绝、非法 expansion、去重），以 `pytest tests/test-benchmark` 全过验证

## 4. 首轮环实测（需账单确认后提交）

- [x] 4.1 环联调（Baihua 至多 4 环、Shenglian 至多 9 环 × 4 电路 × 1024 shots，先打印账单确认再提交），以全部可用环拿到完整 4 份计数验证
- [x] 4.2 输出 ring 推荐报告并与 chain 报告对照（只对照不混排），以报告落盘验证
