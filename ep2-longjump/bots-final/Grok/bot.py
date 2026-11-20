"""Approach gait copied from skills/approach, then one takeoff."""
import math

START_X = 7.0
TARGET = 11.78
STRIDE_PERIOD = 0.9427686943729606
HIP_SWING = 0.4612432499188154
KNEE_BASE = 0.0
KNEE_SWING = 0.46037503371745675
HIP_OFFSET = 0.16081091205893727
LEG_OFFSET = -0.11176635736923067
KNEE_PHASE = -0.6848588702859837
BALANCE_GAIN = -0.8549383067209367
BALANCE_DAMPING = -0.019411040799691966

_phase = "run"
_t0 = None
_air = 0


def _clip(v):
    if not math.isfinite(v):
        return 0.0
    return -1.0 if v < -1.0 else (1.0 if v > 1.0 else v)


def _ang(u, lo, span):
    return math.radians(lo + (u + 1.0) * 0.5 * span)


def _toe(obs, side):
    j = obs["joints"]
    torso = -obs["torso_angle"]
    thigh = torso + _ang(j[0 if side == 0 else 2], -60.0, 135.0)
    shin = thigh + _ang(j[1 if side == 0 else 3], -110.0, 110.0)
    ank = j[6 + side] if len(j) > 6 + side else -0.09
    foot = shin + _ang(ank, -50.0, 110.0)
    x = obs["x"] + math.sin(torso) * 0.30
    x += math.sin(thigh) * 0.45 + math.sin(shin) * 0.45
    x += 0.22 * math.cos(foot) + 0.02 * math.sin(foot)
    return x


def gait(obs):
    phase = obs["time"] * math.tau / STRIDE_PERIOD
    ramp = min(1.0, obs["time"] / 0.5)
    legs, ankles = [], []
    for side in (0, 1):
        leg_phase = phase + side * math.pi
        swing = ramp * HIP_SWING * math.cos(leg_phase) + LEG_OFFSET
        knee = KNEE_BASE - ramp * KNEE_SWING * max(0.0, -math.sin(leg_phase + KNEE_PHASE))
        hip = swing + HIP_OFFSET + BALANCE_GAIN * obs["torso_angle"] + BALANCE_DAMPING * obs["torso_omega"]
        ankle = -swing - knee
        legs.extend([(hip * 180.0 / math.pi - 7.5) / 67.5, 1.0 + knee * 180.0 / math.pi / 55.0])
        ankles.append((ankle * 180.0 / math.pi - 5.0) / 55.0)
    return [_clip(v) for v in (legs + [-0.39, -0.39] + ankles)]


def _plant_now(obs):
    c = obs["contact"]
    toes = []
    swing = []
    if c["foot_L"]:
        toes.append(_toe(obs, 0))
    else:
        swing.append(_toe(obs, 0))
    if c["foot_R"]:
        toes.append(_toe(obs, 1))
    else:
        swing.append(_toe(obs, 1))
    if not toes:
        return False
    stance = max(toes)
    ahead = max(swing) if swing else stance
    ang = obs["torso_angle"]
    y = obs["y"]
    vy = obs["vy"]
    stable = ang < 0.70 and y > 0.90 and vy > -1.8
    if stance >= TARGET and stable:
        return True
    if stance >= 11.34 and ahead >= 12.01 and stable:
        return True
    if stance >= 11.92:
        return True
    if obs["x"] >= 11.55 or obs["time"] >= 16.0:
        return True
    return False


def act(obs):
    global _phase, _t0, _air
    ang = obs["torso_angle"]
    y = obs["y"]
    vy = obs["vy"]
    t = obs["time"]
    feet = obs["contact"]["foot_L"] or obs["contact"]["foot_R"]

    if _phase == "run":
        if _plant_now(obs):
            _phase = "crouch"
            _t0 = t
        else:
            return gait(obs)

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
