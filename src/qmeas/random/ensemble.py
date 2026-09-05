from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

# 每行为测量轴 n̂ 的布洛赫角 (θ, φ), 对应待测 Pauli X / Y / Z;
# 也是 add_meas 中 u(-θ, 0, -φ) 门的输入角。
PAULI_ROTATIONS = np.array(
    [
        [np.pi / 2, 0],
        [np.pi / 2, np.pi / 2],
        [0, 0],
    ],
    dtype=float,
)

AngleSampler = Callable[
    [int, int, np.random.Generator],
    tuple[np.ndarray, np.ndarray],
]


def _sample_haar(group_num, num_settings, rng):
    theta = np.arccos(rng.uniform(-1.0, 1.0, size=(group_num, num_settings)))
    phi = rng.uniform(0.0, 2.0 * np.pi, size=(group_num, num_settings))
    return theta, phi


def _sample_pauli(group_num, num_settings, rng):
    idx = rng.choice(3, size=(group_num, num_settings))
    return PAULI_ROTATIONS[idx, 0], PAULI_ROTATIONS[idx, 1]


ANGLE_SAMPLERS: dict[str, AngleSampler] = {
    "haar": _sample_haar,
    "pauli": _sample_pauli,
}


@dataclass
class ParameterGenerator:
    angle_sampler: AngleSampler
    rng: np.random.Generator

    def generate(self, params, num_settings):
        theta_vals, phi_vals = self.angle_sampler(
            len(params[0]), num_settings, self.rng
        )
        theta, phi = params
        binds = {theta[i]: theta_vals[i].tolist() for i in range(len(theta))}
        binds.update({phi[i]: phi_vals[i].tolist() for i in range(len(phi))})
        return binds


def create_parameter_generator(ensemble, *, seed=None):
    try:
        sampler = ANGLE_SAMPLERS[ensemble]
    except KeyError:
        raise ValueError(
            f"Unknown ensemble: {ensemble!r}. Available: {list(ANGLE_SAMPLERS)}"
        ) from None

    return ParameterGenerator(sampler, np.random.default_rng(seed))


def conjugate_binds(binds, params, i1_groups):
    """由实验一的角度绑定构造实验二的绑定：I_1 区各 group 的 φ 取反。

    电路实际施加 u(-θ, 0, -φ)，而 conj(u(-θ, 0, -φ)) = u(-θ, 0, +φ)，
    故复共轭等价于 φ 变号、θ 不变。不消耗随机数，可复现。
    """
    _, phi = params
    new_binds = dict(binds)
    for g in i1_groups:
        p = phi[g]
        new_binds[p] = [-v for v in binds[p]]
    return new_binds
