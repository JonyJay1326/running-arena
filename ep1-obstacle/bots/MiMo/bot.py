import math

# ================== 调参区 ==================
# v11（最好成绩 1.66m）：温和 bound + 倾角>20° 紧急直腿纠偏
PERIOD = 0.6
REF_ANG = 0.08
KP_HIP = 2.6
KD_HIP = 0.85
HIP_BAL_LIM = 0.9
KP_SH = 1.6
KD_SH = 0.5
SH_BAL_LIM = 1.0

HIP_KEYS = [(0.0, 0.15), (0.70, -0.30), (0.85, 0.35), (1.0, 0.15)]
KNEE_KEYS = [(0.0, 0.85), (0.15, 0.65), (0.55, 0.95), (0.70, 1.00),
             (0.82, -0.20), (0.95, 0.55), (1.0, 0.85)]
SH_KEYS = [(0.0, 0.25), (0.55, -0.10), (0.85, -0.45), (1.0, 0.25)]

_state = {"init": False, "lost": 0.0, "prev_x": 0.0, "done": False}


def _curve(p, keys):
    if p <= keys[0][0]:
        return keys[0][1]
    for (p0, v0), (p1, v1) in zip(keys, keys[1:]):
        if p <= p1:
            f = (p - p0) / (p1 - p0) if p1 > p0 else 1.0
            f = f * f * (3.0 - 2.0 * f)
            return v0 + (v1 - v0) * f
    return keys[-1][1]


def _is_down(o):
    if o["contact"]["torso"] or o["y"] < 0.45:
        return True
    ang = (o["torso_angle"] + math.pi) % (2 * math.pi) - math.pi
    return abs(ang) >= math.radians(50) and o["y"] < 0.70


def act(obs):
    s = _state
    x = obs["x"]
    if not s["init"]:
        s["init"] = True
        s["prev_x"] = x
    if _is_down(obs):
        s["lost"] += x - s["prev_x"]
    s["prev_x"] = x
    if x - max(s["lost"], 0.0) >= 18.05:
        s["done"] = True

    ang = obs["torso_angle"]
    omega = obs["torso_omega"]

    if s["done"]:
        return [1.0, 0.85, 1.0, 0.85, -0.39, -0.39]

    # 紧急纠偏：倾角超 0.35rad 时放弃步态，双腿伸直做强力倾角伺服
    if abs(ang - REF_ANG) > 0.35:
        hip = -3.5 * (ang - REF_ANG) - 1.8 * omega
        if hip > 1.0:
            hip = 1.0
        elif hip < -1.0:
            hip = -1.0
        sh = -3.0 * (ang - REF_ANG) - 1.5 * omega
        if sh > 1.0:
            sh = 1.0
        elif sh < -1.0:
            sh = -1.0
        return [hip, 0.85, hip, 0.85, sh, sh]

    p = (obs["time"] / PERIOD) % 1.0

    bal = KP_HIP * (REF_ANG - ang) - KD_HIP * omega
    if bal > HIP_BAL_LIM:
        bal = HIP_BAL_LIM
    elif bal < -HIP_BAL_LIM:
        bal = -HIP_BAL_LIM
    shbal = KP_SH * (REF_ANG - ang) - KD_SH * omega
    if shbal > SH_BAL_LIM:
        shbal = SH_BAL_LIM
    elif shbal < -SH_BAL_LIM:
        shbal = -SH_BAL_LIM

    hip = _curve(p, HIP_KEYS) + bal
    knee = _curve(p, KNEE_KEYS)
    sh = _curve(p, SH_KEYS) + shbal
    return [hip, knee, hip, knee, sh, sh]
