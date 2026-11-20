"""Shuffle to the board, one takeoff, then correct flight lean before the feet land."""
import math

START_X = 9.0

_phase = "run"
_t0 = None
_armed = False
_air = 0


def _clip(v):
    if not math.isfinite(v):
        return 0.0
    return -1.0 if v < -1.0 else (1.0 if v > 1.0 else v)


def _shuffle(obs):
    ang = obs["torso_angle"]
    om = obs["torso_omega"]
    hip = -0.11 - 1.5 * (ang - 0.15) - 0.12 * om
    ank = -1.6 * (ang - 0.15) - 0.05 * om
    c = _clip
    return [c(hip), 0.9, c(hip), 0.9, -0.39, -0.39, c(ank), c(ank)]


def act(obs):
    global _phase, _t0, _armed, _air
    ang = obs["torso_angle"]
    x = obs["x"]
    y = obs["y"]
    vy = obs["vy"]
    t = obs["time"]
    feet = obs["contact"]["foot_L"] or obs["contact"]["foot_R"]

    if _phase == "run":
        if x >= 10.9:
            _armed = True
        plant = feet and ang < 0.40 and y > 1.02 and vy > -1.1
        if (_armed and plant) or x >= 11.25 or t >= 14.0:
            _phase = "crouch"
            _t0 = t
        else:
            return _shuffle(obs)

    elapsed = t - _t0
    if _phase == "crouch":
        ready = elapsed >= 0.331 and ang >= 0.74 and vy <= 1.829
        if ready or elapsed >= 0.9:
            _phase = "extend"
            _t0 = t
        else:
            return [1.0, -0.70, 1.0, -0.70, 0.051, 0.051, 1.0, 1.0]

    if not feet:
        _air += 1
    else:
        _air = 0
    if _air > 4 and vy < -1.5 and y < 1.10 and ang > -1.0:
        return [-0.85, -0.15, -0.85, -0.15, 0.4, 0.4, -0.2, -0.2]
    if _air > 6 and ang < 0.60:
        return [0.05, -0.9, 0.05, -0.9, -1.0, -1.0, 0.7, 0.7]
    if _air > 6 and vy < 0.0 and y < 1.25 and ang > 0.0:
        return [-0.75, 0.85, -0.75, 0.85, 0.15, 0.15, 0.35, 0.35]
    return [-0.391, 0.639, -0.391, 0.639, 0.688, 0.688, -0.342, -0.342]
