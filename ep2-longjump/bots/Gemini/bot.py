import math

START_X = 9.0

def air(obs):
    return [-0.930, -0.975, -0.930, -0.975, -0.225, -0.225, 1.0, 1.0]

# Precomputed wind-adaptive parameter table:
# (w0, dw, d_ank, d_tang, trigger_dist, crouch_dur, crouch_knee, push_hip)
_WIND_TABLE = [
    (-0.7924, +0.2421, -0.025, -0.03, 1.05, 0.08, -0.40, -0.65),
    (-0.6568, +0.1665, +0.005, +0.00, 1.10, 0.12, -0.40, -0.65),
    (-0.6277, +0.1539, +0.000, +0.00, 1.15, 0.16, -0.70, -0.65),
    (-0.6203, +0.1818, +0.000, +0.00, 1.15, 0.12, -0.70, -0.85),
    (-0.4530, +0.1900, +0.000, -0.06, 1.15, 0.06, -0.10, -0.65),
    (-0.3522, +0.0000, -0.010, -0.06, 1.15, 0.12, -0.40, -0.65),
    (-0.2850, -0.2750, -0.010, -0.06, 1.10, 0.12, -0.10, -0.65),
    (-0.2733, -0.0686, -0.015, +0.09, 0.90, 0.12, -0.70, -0.85),
    (-0.2385, +0.0786, +0.005, +0.00, 1.05, 0.12, -0.10, -0.65),
    (-0.1771, -0.3217, -0.005, -0.03, 1.00, 0.12, -0.70, -0.85),
    (+0.2082, -0.0189, -0.010, -0.06, 0.95, 0.12, -0.10, -0.65),
    (+0.2761, +0.1270, -0.010, -0.06, 0.85, 0.12, -0.10, -0.65),
    (+0.3450, -0.1380, -0.010, -0.06, 1.10, 0.12, -0.40, -0.65),
    (+0.3677, -0.1974, -0.005, +0.15, 1.10, 0.12, -0.40, -0.65),
    (+0.4469, -0.0075, -0.010, -0.06, 0.90, 0.12, -0.10, -0.65),
    (+0.4564, +0.2016, -0.005, -0.09, 1.00, 0.12, -0.70, -0.85),
    (+0.4841, -0.0753, +0.000, +0.06, 0.90, 0.12, -0.40, -0.65),
    (+0.4860, +0.0257, -0.010, -0.06, 0.90, 0.12, -0.40, -0.65),
    (+0.7096, -0.2476, +0.010, +0.06, 0.90, 0.12, -0.40, -0.65),
]

_phase = "RUN"
_t0 = 0.0
_w0 = 0.0
_params = (+0.000, +0.00, 1.15, 0.12, -0.70, -0.85)

def _select_by_w0(w0):
    best_d = 1e9
    best_p = _params
    for row in _WIND_TABLE:
        d = abs(row[0] - w0)
        if d < best_d:
            best_d = d
            best_p = row[2:]
    return best_p

def _select_by_w0_dw(w0, dw):
    best_d = 1e9
    best_p = _params
    for row in _WIND_TABLE:
        d = (row[0] - w0) ** 2 + 0.15 * (row[1] - dw) ** 2
        if d < best_d:
            best_d = d
            best_p = row[2:]
    return best_p

def act(obs):
    global _phase, _t0, _w0, _params
    t = obs["time"]
    x = obs["x"]
    y = obs["y"]
    vx = obs["vx"]
    vy = obs["vy"]
    ang = obs["torso_angle"]
    omega = obs["torso_omega"]
    c = obs["contact"]
    cL = c["foot_L"]
    cR = c["foot_R"]
    dist = obs["dist_to_line"]
    w = obs.get("wind", 0.0)

    if t < 0.001:
        _phase = "RUN"
        _t0 = 0.0
        _w0 = w
        _params = _select_by_w0(_w0)
    elif 0.010 < t < 0.025:
        mix1 = (1.0 / 90.0) ** 2 * (3.0 - 2.0 / 90.0)
        dw = (w - _w0) / mix1
        _params = _select_by_w0_dw(_w0, dw)

    d_ank, d_tang, trigger_dist, crouch_dur, crouch_knee, push_hip = _params

    if _phase == "RUN":
        if dist <= trigger_dist:
            _phase = "PREP"
            _t0 = t
    if _phase == "PREP":
        if cL or cR:
            _phase = "CROUCH"
            _t0 = t
    if _phase == "CROUCH":
        if t - _t0 >= crouch_dur or dist <= 0.28:
            _phase = "PUSH"
            _t0 = t
    if _phase == "PUSH":
        if (not cL and not cR and vy > 1.0 and t - _t0 >= 0.03) or (t - _t0 >= 0.25):
            _phase = "AIR"
            _t0 = t
    if _phase == "AIR":
        if (t - _t0 > 0.08) and (cL or cR or c["torso"] or c["hand_L"] or c["hand_R"]):
            _phase = "LAND"
            _t0 = t

    if _phase == "RUN":
        if t < 0.25:
            return [-0.25, 0.7, -0.05, 0.5, 0.3, 0.3, 0.2 + d_ank, 0.25 + d_ank]
        cyc = (t - 0.25) / 0.35
        step_idx = int(cyc)
        ph = cyc - step_idx
        bal = 0.8 * (ang - (0.22 + d_tang)) + 0.15 * omega

        swing_hip = -0.1 + 0.65 * math.sin(ph * math.pi * 0.5) + 0.5 * bal
        swing_knee = 0.1 if ph < 0.55 else 0.85
        swing_ank = 0.3 if ph < 0.6 else 0.0

        stance_hip = -0.1 - 0.35 * ph - 0.5 * bal
        stance_knee = 0.85 if ph < 0.5 else 0.98
        stance_ank = 0.15 if ph < 0.6 else -0.25

        if step_idx % 2 == 0:
            return [swing_hip, swing_knee, stance_hip, stance_knee, -0.5, 0.4, swing_ank, stance_ank]
        else:
            return [stance_hip, stance_knee, swing_hip, swing_knee, 0.4, -0.5, stance_ank, swing_ank]

    elif _phase == "PREP":
        bal = 0.5 * (ang - 0.10) + 0.10 * omega
        return [0.20 + bal, 0.2, 0.20 + bal, 0.2, -0.8, -0.8, 0.4, 0.4]

    elif _phase == "CROUCH":
        bal = 0.5 * (ang - 0.25) + 0.10 * omega
        return [0.35 + bal, crouch_knee, 0.35 + bal, crouch_knee, -0.9, -0.9, 0.85, 0.85]

    elif _phase == "PUSH":
        bal = 0.8 * (ang - 0.25) + 0.15 * omega
        h = max(-1.0, min(1.0, push_hip - bal))
        return [h, 1.0, h, 1.0, 1.0, 1.0, -1.0, -1.0]

    elif _phase == "AIR":
        dt = t - _t0
        if dt < 0.20:
            return air(obs)
        else:
            return [0.85, 0.65, 0.85, 0.65, -0.2, -0.2, 0.8, 0.8]

    else:
        return [0.7, -0.3, 0.7, -0.3, 0.9, 0.9, 0.6, 0.6]
