import math

_T = 0.52
_AMP = 0.50
_KP = 2.0
_KD = 0.45
_A_REF = 0.05
_mode = 1.0        # +1 正常前蹬, -1 倒踩救倾（前倾过大时反向迈步）


def _c(v):
    return max(-1.0, min(1.0, v))


def act(obs):
    global _mode
    t = obs['time']
    a = obs['torso_angle']
    w = obs['torso_omega']

    if a > 0.32:
        _mode = -1.0
    elif a < 0.12:
        _mode = 1.0

    u = _c(-(_KP * (a - _A_REF) + _KD * w))

    near = obs['next_bump_dist'] < 0.9 and obs['next_bump_height'] > 0.05
    if near:
        per, amp, koff, kamp = 0.44, 0.58, 0.15, 0.85
    else:
        per, amp, koff, kamp = _T, _AMP, 0.5, 0.5

    ramp = min(1.0, t / 0.8)
    th = 2.0 * math.pi * (t % per) / per
    amp = amp * ramp * _mode
    s = math.sin(th)
    cs = math.cos(th)

    hL = _c(u + amp * s)
    hR = _c(u - amp * s)
    kL = _c(koff - kamp * cs * _mode)
    kR = _c(koff + kamp * cs * _mode)

    sh = 0.5 * s * _mode
    return [hL, kL, hR, kR, _c(-0.39 + sh), _c(-0.39 - sh)]
