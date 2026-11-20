import math


_p = 0.0
_frames = 0


def act(obs):
    global _p, _frames
    ang = obs['torso_angle']
    w = obs['torso_omega']
    x = obs['x']
    if x >= 18.2:
        return [0.3, -0.7, 0.3, -0.7, 0.8, 0.8]

    if _frames < 72:
        _frames += 1
        bal = max(-0.3, min(0.3, 1.0*ang + 0.06*w))
        return [bal, 1.0, bal, 1.0, 0.8, 0.8]

    bal = max(-0.3, min(0.3, 1.0 * ang + 0.06 * w))
    f = 0.9
    _p = (_p + f / 60.0) % 1.0
    p = _p
    if p < 0.35:
        return [0.15 + bal, -0.5, 0.15 + bal, -0.5, 0.8, 0.8]
    elif p < 0.5:
        return [0.62 + bal, 1.0, 0.62 + bal, 1.0, 0.8, 0.8]
    else:
        q = (p - 0.5) / 0.5
        hip = 0.35 - 0.2 * q
        return [hip + bal, 0.7, hip + bal, 0.7, 0.8, 0.8]










