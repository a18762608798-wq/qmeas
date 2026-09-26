from dataclasses import dataclass, field
from pathlib import Path

from ..random.config import QuarkOptions


@dataclass
class BenchmarkConfig:
    """选机选比特基准的顶层配置。

    chips 只含 Baihua 与 Shenglian（Dongling 确认最差，不测）。
    runner_opts 为提交/轮询/重试的基线，实际提交时按 (chip, target_qubits)
    派生副本；chain_score = w_stab * mean(<S_i>) + w_ro * readout_fid。
    """

    chips: list[str] = field(default_factory=lambda: ["Baihua", "Shenglian"])
    chain_length: int = 8
    max_chains_per_chip: int = 3
    shots: int = 1024
    w_stab: float = 0.8
    w_ro: float = 0.2
    # 候选链扩展策略：conservative（一跳替换） | slide（等长滑动）
    #   | multi-seed（邻近链长成种） | sample（全图随机游走采样）
    expansion: str = "conservative"
    # 候选拓扑形状：chain（链） | ring（环，只支持偶数长度）。
    # ring 自成一次推荐，只内部比较，不与 chain 混排。
    shape: str = "chain"
    output_dir: Path = field(default_factory=lambda: Path("./data_benchmark"))
    name: str = "qubit_benchmark"
    # backend() 调用节流：连续两次拉取的最短间隔（秒）
    fetch_throttle_interval: float = 60.0
    runner_opts: QuarkOptions = field(default_factory=QuarkOptions)

    def __post_init__(self) -> None:
        self.output_dir = Path(self.output_dir)
        if self.chain_length < 2:
            raise ValueError("chain_length 至少为 2")
        if not self.chips:
            raise ValueError("chips 不能为空")
        if abs(self.w_stab + self.w_ro - 1.0) > 1e-9:
            raise ValueError("w_stab + w_ro 必须为 1")
        if self.expansion not in (
            "conservative", "slide", "multi-seed", "sample"
        ):
            raise ValueError(
                "expansion 必须为 conservative/slide/multi-seed/sample"
                f" 之一，当前 {self.expansion!r}"
            )
        if self.shape not in ("chain", "ring"):
            raise ValueError(
                f"shape 必须为 chain/ring 之一，当前 {self.shape!r}"
            )
        if self.shape == "ring" and self.chain_length % 2:
            raise ValueError(
                f"ring 只支持偶数长度，当前 chain_length={self.chain_length}"
            )
