# Tasks

## 1. 配置与拓扑层

- [x] 1.1 新建 `src/qmeas/benchmark/config.py`（`BenchmarkConfig`：chips=[Baihua, Shenglian]、chain_length=8、max_chains_per_chip=3、shots=1024、权重 0.8/0.2），以 `python -c "from qmeas.benchmark.config import BenchmarkConfig; BenchmarkConfig()"` 导入成功验证
- [x] 1.2 实现 `topology.py`（`import quark` 前强制 `matplotlib.use('Agg')`、单次 `backend()` 拉取 + json 缓存 + 60s 节流），以本地缓存文件生成、60s 内二次调用零新增网络请求、无头环境下 `backend()` 秒级返回验证
- [x] 1.3 实现候选链生成与硬过滤（`priority_qubits` 种子 + 一跳扩展，丢弃死比特/零边链），以用户已提供的 Baihua/Shenglian 真实快照为输入、断言 8 链种子 `[128, 129, 142, 141, 140, 139, 138, 137]` 与 `[74, 81, 75, 82, 76, 69, 62, 68]` 通过过滤验证

## 2. 基准电路与打分

- [x] 2.1 实现 `circuits.py`（H + 7 CZ 制备，奇偶 2 组稳定子测量设置 + all-0/all-1），以 Aer 本地跑通 4 电路、无噪声下稳定子均值≈1 验证
- [x] 2.2 实现 `scoring.py`（复用 `estimator` 的 `group_qubitwise` + `QubitwiseBasis.recover`，`chain_score = 0.8*mean(<S_i>) + 0.2*readout_fid`），以构造的模拟计数字典算出预期分数验证

## 3. 运行器与输出

- [x] 3.1 实现 `runner.py`（复用 `quark_client` 提交/轮询/重提，`target_qubits`=链、`coupling_map`=链边，两机各自 gather，每份结果即时落盘 checkpoint），以 mock `Task` 的离线单元测试验证任务组装与结果回收，并以模拟中途中断后重跑只补缺失任务验证断点续跑
- [x] 3.2 实现 `io.py` 与 `recommend()`（跨机排序、evidence 含分数/tids/校准时间/排队深度），以模拟的两机分数输入返回正确第一名验证
- [x] 3.3 更新 `src/qmeas/__init__.py` 导出与 `README.md` 模块说明，以 `python -c "import qmeas.benchmark"` 成功验证

## 4. 测试与首轮真机验证

- [x] 4.1 补 `tests/test-benchmark/` 单元测试（过滤逻辑、打分公式、缓存节流），以 `pytest tests/test-benchmark` 全过验证
- [x] 4.2 首轮真机联调（Baihua + Shenglian 各 ≤3 链 × 4 电路 × 1024 shots，先打印账单确认再提交），以两台机各至少一条链拿到完整 4 份计数验证
- [x] 4.3 确认 `target_qubits` 是否按映射执行并输出首份推荐报告，若为自动布局则按 design 风险项回炉，以书面结论（pin 住 / 未 pin 住）验证

## 5. 扩展策略（四档）

- [x] 5.1 `config.py` 加 `expansion` 字段（`conservative`/`slide`/`multi-seed`/`sample`，默认 `conservative`，非法值抛错），以非法值抛错与默认值验证
- [x] 5.2 实现 `slide`（种子等长滑动），以 Baihua 种子滑出至少 3 条有效变体验证
- [x] 5.3 实现 `multi-seed`（邻近链长截长/补短成种），以 Baihua fixture 的 7/9/10 链产出 8 链种子验证
- [x] 5.4 实现 `sample`（分区域随机游走 + 静态预排序取前 N），以采样 N 条全有效且去重验证
- [x] 5.5 补 pytest（四档各至少一例，含非法值），以 `pytest tests/test-benchmark` 全过验证

## 6. 用户示例

- [x] 6.1 新增 `example/benchmark/quark_benchmark_example.py`（仿照 `example/random/quark_example.py` 风格显式写出全部选项并逐行注释：chips/chain_length/max_chains_per_chip/shots/w_stab/w_ro/expansion/output_dir/name/fetch_throttle_interval/runner_opts；流程含 bill 打印→run_benchmark→recommend→save_report→target_qubits 回填 `QuarkOptions`），以 py_compile 通过 + 逐项注释核对无遗漏验证（不实际提交真机任务）
