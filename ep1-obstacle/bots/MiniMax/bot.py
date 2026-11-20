import math

D = math.pi / 180.0
HIP_LO, HIP_SPAN = -60 * D, 135 * D
KNEE_LO, KNEE_SPAN = -110 * D, 105 * D
SH_LO, SH_SPAN = -70 * D, 230 * D

K_ANG = 0.35         # 髋策略增益
LEAN_MAX = 70.0 * D  # 前倾目标：驱动躯干前倾，让重心越过脚尖
ARM_BASE = 60.0 * D  # 手臂前摆角度


def _clamp(v, lo=-1.0, hi=1.0):
    return lo if v < lo else (hi if v > hi else v)


def _hip(rel):
    return _clamp(2.0 * (rel - HIP_LO) / HIP_SPAN - 1.0)


def _knee(flex):
    return _clamp(2.0 * ((-flex) - KNEE_LO) / KNEE_SPAN - 1.0)


def _sh(rel):
    return _clamp(2.0 * (rel - SH_LO) / SH_SPAN - 1.0)


def _smooth(u):
    u = _clamp(u, 0.0, 1.0)
    return u * u * (3.0 - 2.0 * u)


def act(obs: dict) -> list[float]:
    t = obs['time']
    ang = obs['torso_angle']
    # 0~1.0s：靠开局髋前顶(14°/0.2s)抵消膝关节初始超限的后冲量，先站稳
    # 1.0~2.6s：把髋目标逐步后拉，躯干随之前倾，重心越过脚尖 -> 向���扑倒
    lean = -LEAN_MAX * _smooth((t - 0.8) / 1.2)
    base = 14.0 * D * math.exp(-t / 0.20)
    rel = base + K_ANG * (ang - lean)
    arm = ARM_BASE * _smooth(t / 0.8)
    f = int(round(t * 60.0))
    if f % 30 == 0 and t < 12.0:
        print("DBG t=%.2f x=%.3f y=%.3f vx=%.3f ang=%+.3f hipL=%+.2f kneeL=%+.2f fL=%d fR=%d tq=%d"
              % (t, obs['x'], obs['y'], obs['vx'], ang, obs['joints'][0], obs['joints'][1],
                 obs['contact']['foot_L'], obs['contact']['foot_R'], obs['contact']['torso']))
    return [_hip(rel), _knee(5.0 * D), _hip(rel), _knee(5.0 * D), _sh(arm), _sh(arm)]
