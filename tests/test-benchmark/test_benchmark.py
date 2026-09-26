"""qmeas.benchmark 离线单元测试：不过真机（mock Task / 本地 fixture）。"""

import asyncio
import json
import shutil
import time
from pathlib import Path

import pytest

from qmeas.benchmark.circuits import build_circuits
from qmeas.benchmark.config import BenchmarkConfig
from qmeas.benchmark.io import recommend
from qmeas.benchmark.runner import bill, run_benchmark
from qmeas.benchmark.scoring import ChainResult, score_chain
from qmeas.benchmark.topology import (
    _last_fetch,
    build_chains,
    chain_is_valid,
    fetch_topology,
)
FIX = Path(__file__).resolve().parent / "fixtures"


def _load(name):
    with (FIX / name).open(encoding="utf-8") as f:
        return json.load(f)


def test_seed_chains_pass_filter():
    bai = _load("baihua_backend.json")
    she = _load("shenglian_backend.json")
    cb, _ = build_chains(bai, 8, 3)
    cs, _ = build_chains(she, 8, 3)
    assert cb[0] == [128, 129, 142, 141, 140, 139, 138, 137]
    assert cs[0] == [74, 81, 75, 82, 76, 69, 62, 68]
    assert all(chain_is_valid(c, bai) for c in cb)
    assert all(chain_is_valid(c, she) for c in cs)


def test_dead_and_zero_edge_filtered():
    bai = _load("baihua_backend.json")
    # Q66 死比特；C1 [1,2] 零保真边；重复节点无自环边
    assert not chain_is_valid([65, 66, 67, 68, 69, 70, 71, 72], bai)
    assert not chain_is_valid([1, 2, 3, 4, 5, 6, 7, 8], bai)
    assert not chain_is_valid([0, 1, 0, 1, 0, 1, 0, 1], bai)


def test_scoring_formula():
    cs = build_circuits(8)
    perfect = {"stab_g0": {"00000000": 1024}, "stab_g1": {"00000000": 1024},
               "allzero": {"00000000": 1024}, "allone": {"11111111": 1024}}
    m, f, s = score_chain(perfect, cs.stab_groups, 1024)
    assert (m, f, s) == (1.0, 1.0, 1.0)
    deg = dict(perfect)
    deg["allzero"] = {"00000000": 512, "00000001": 512}
    m2, f2, s2 = score_chain(deg, cs.stab_groups, 1024)
    assert abs(f2 - 0.75) < 1e-9
    assert abs(s2 - (0.8 * m2 + 0.2 * 0.75)) < 1e-9


def test_throttle_and_cache(tmp_path, monkeypatch):
    import quark

    calls = {"n": 0}

    class MockTask:
        def __init__(self, token):
            pass

        def backend(self, chip):
            calls["n"] += 1
            return {"calibration_time": "t0", "qubits_info": {},
                    "couplers_info": {}, "priority_qubits": []}

    monkeypatch.setattr(quark, "Task", MockTask)
    cfg = BenchmarkConfig(output_dir=tmp_path)
    cfg.runner_opts.token = "dummy"
    info = fetch_topology(cfg, "Baihua")
    assert calls["n"] == 1 and info["calibration_time"] == "t0"
    # 二次调用走缓存，零新增请求
    fetch_topology(cfg, "Baihua")
    assert calls["n"] == 1
    # 无缓存 + 节流窗口内 → 拒绝调用
    _last_fetch["NoChip"] = time.monotonic()
    with pytest.raises(RuntimeError):
        fetch_topology(cfg, "NoChip")


class _MockTask:
    def __init__(self, token):
        self.tid = 0
        self.names = {}
        self.runs = 0

    def status(self):
        return {"Baihua": 25}

    def run(self, task):
        self.runs += 1
        self.tid += 1
        self.names[self.tid] = task["name"]
        return self.tid

    def result(self, tid):
        key = self.names[tid].rsplit("_", 1)[-1]
        hist = {"11111111": 1024} if key == "allone" else {"00000000": 1024}
        return {"count": hist}


def _seed_cache(out: Path):
    cdir = out / "topology_cache"
    cdir.mkdir(parents=True)
    shutil.copy(FIX / "baihua_backend.json",
                cdir / "Baihua_2026_09_23_10_39_14.json")


def test_runner_and_resume(tmp_path, monkeypatch):
    from qmeas.random import quark_client

    mock = _MockTask("dummy")
    monkeypatch.setattr(quark_client, "Task", lambda token: mock)
    out = tmp_path / "run"
    cfg = BenchmarkConfig(output_dir=out, max_chains_per_chip=1)
    cfg.runner_opts.token = "dummy"
    _seed_cache(out)
    chains = {"Baihua": [[128, 129, 142, 141, 140, 139, 138, 137]]}
    assert bill(cfg, chains)["total_tasks"] == 4
    res = asyncio.run(run_benchmark(cfg, chains))
    assert mock.runs == 4
    assert sorted(res[0].counts) == ["allone", "allzero", "stab_g0", "stab_g1"]
    # 模拟中断：删掉 allone 后重跑，只补 1 个
    ckp = out / "checkpoints" / "qubit_benchmark_Baihua_c0.json"
    ck = json.loads(ckp.read_text(encoding="utf-8"))
    ck["counts"].pop("allone")
    ck["tids"].pop("allone")
    ckp.write_text(json.dumps(ck), encoding="utf-8")
    mock.runs = 0
    res2 = asyncio.run(run_benchmark(cfg, chains))
    assert mock.runs == 1
    assert sorted(res2[0].counts) == ["allone", "allzero", "stab_g0", "stab_g1"]


def test_recommend_ranking():
    cs = build_circuits(8)
    good = {"stab_g0": {"00000000": 1024}, "stab_g1": {"00000000": 1024},
            "allzero": {"00000000": 1024}, "allone": {"11111111": 1024}}
    bad = dict(good)
    bad["stab_g0"] = {"00000000": 700, "11111111": 324}
    r1 = ChainResult(chip="Baihua", chain=[1] * 8, shots=1024, counts=good)
    r2 = ChainResult(chip="Shenglian", chain=[2] * 8, shots=1024, counts=bad)
    chip, _, ev = recommend([r1, r2], cs)
    assert chip == "Baihua"
    assert ev["chains"][0]["score"] >= ev["chains"][1]["score"]
    with pytest.raises(RuntimeError):
        recommend([], cs)


def test_expansion_illegal_value():
    with pytest.raises(ValueError):
        BenchmarkConfig(expansion="bogus")
    assert BenchmarkConfig().expansion == "conservative"


def test_expansion_slide():
    bai = _load("baihua_backend.json")
    ch, _ = build_chains(bai, 8, 5, "slide")
    assert len(ch) >= 4  # 种子 + 至少 3 变体
    assert ch[0] == [128, 129, 142, 141, 140, 139, 138, 137]
    assert all(chain_is_valid(c, bai) for c in ch)
    assert len({tuple(c) for c in ch}) == len(ch)


def test_expansion_multi_seed():
    bai = _load("baihua_backend.json")
    ch, _ = build_chains(bai, 8, 5, "multi-seed")
    assert ch[0] == [128, 129, 142, 141, 140, 139, 138, 137]
    # 含 9 链截出的 [129..136] 与 10 链截出的 [142..149]
    assert [129, 142, 141, 140, 139, 138, 137, 136] in ch
    assert [142, 141, 140, 139, 138, 137, 136, 149] in ch
    assert all(chain_is_valid(c, bai) for c in ch)


def test_expansion_sample():
    bai = _load("baihua_backend.json")
    she = _load("shenglian_backend.json")
    for info in (bai, she):
        ch, _ = build_chains(info, 8, 5, "sample")
        assert len(ch) == 5
        assert all(chain_is_valid(c, info) for c in ch)
        assert len({tuple(c) for c in ch}) == 5
    # 确定性：同种子同结果
    ch1, _ = build_chains(bai, 8, 5, "sample")
    ch2, _ = build_chains(bai, 8, 5, "sample")
    assert ch1 == ch2


def test_ring_enumeration_counts():
    from qmeas.benchmark.topology import find_rings, ring_is_valid

    bai = _load("baihua_backend.json")
    she = _load("shenglian_backend.json")
    rb = find_rings(bai, 10)
    rs = find_rings(she, 10)
    assert len(rb) == 4
    assert len(rs) == 9
    assert all(ring_is_valid(r, bai) for r in rb)
    assert all(ring_is_valid(r, she) for r in rs)
    # 去重：无旋转/翻转重复
    assert len({tuple(r) for r in rb}) == 4
    with pytest.raises(ValueError):
        find_rings(bai, 9)


def test_ring_dispatch_and_rejection():
    bai = _load("baihua_backend.json")
    ch, _ = build_chains(bai, 10, 3, "sample", "ring")
    assert 1 <= len(ch) <= 3
    assert ch[0] == [125, 126, 127, 128, 129, 142, 141, 140, 139, 138]
    with pytest.raises(ValueError):
        build_chains(bai, 10, 3, "slide", "ring")
    with pytest.raises(ValueError):
        build_chains(bai, 9, 3, "sample", "ring")
    with pytest.raises(ValueError):
        BenchmarkConfig(shape="ring", chain_length=9)


def test_ring_circuits_noiseless():
    from qiskit_aer import AerSimulator

    from qmeas.benchmark.circuits import build_circuits
    from qmeas.estimator.basis import QubitwiseBasis

    cs = build_circuits(10, ring=True)
    assert len(cs.stab_groups) == 2
    sim = AerSimulator()
    rec = QubitwiseBasis()
    for key in ("stab_g0", "stab_g1"):
        counts = sim.run(cs.circuits[key], shots=1024).result().get_counts()
        evs = rec.recover(cs.stab_groups[key], counts, 1024)
        assert abs(sum(evs.values()) / len(evs) - 1.0) < 1e-9
