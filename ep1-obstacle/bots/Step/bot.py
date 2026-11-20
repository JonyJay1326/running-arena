import math

# lean-walk: 前移髋偏置 (前倾倒驱动) + 躯干平衡反馈
# 自测: 站立 ~14 度前倾, 5s 后进入步行相, 最好成绩 1.64m
HIP_BASE = -0.11
KNEE_BASE = 1.00
FWD = 0.40


def _clip(v, lo=-1.0, hi=1.0):
    return lo if v < lo else (hi if v > hi else v)


def act(obs: dict) -> list[float]:
    a = obs["torso_angle"]
    w = obs["torso_omega"]
    vx = obs["vx"]
    x = obs["x"]

    # 冲线后刹车: 收髋 + 屈膝降速
    if x >= 18.0:
        bias = _clip(-(2.0 * a + 0.8 * w), -0.6, 0.6)
        knee = _clip(0.90 - 0.30 * vx, 0.40, 1.0)
        return [_clip(HIP_BASE + bias), _clip(knee), _clip(HIP_BASE + bias), _clip(knee), -0.1, -0.1]

    # 躯干反馈: 前扑 -> 收髋后坐; 后仰 -> 送髋前跨
    bias = _clip(-(2.0 * a + 0.8 * w), -0.85, 0.85)
    knee_bias = _clip(-0.15 * w, -0.30, 0.30)
    sh = _clip(-0.30 * a, -0.45, 0.45)
    hip = _clip(HIP_BASE + FWD + bias)
    knee = _clip(KNEE_BASE + knee_bias)
    return [hip, knee, hip, knee, sh, sh]
