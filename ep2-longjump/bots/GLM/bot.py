import math

START_X = 7.0

STRIDE_PERIOD = 0.9427686943729606
HIP_SWING = 0.4612432499188154
KNEE_BASE = 0.0
KNEE_SWING = 0.46037503371745675
HIP_OFFSET = 0.16081091205893727
LEG_OFFSET = -0.11176635736923067
KNEE_PHASE = -0.6848588702859837
BALANCE_GAIN = -0.8549383067209367
BALANCE_DAMPING = -0.019411040799691966


def pose_deg(L, B, K, D, sh, lean_ref=0.0, obs=None, kp=0.012, kd=0.002):
    fb = 0.0
    if obs is not None:
        fb = kp * (lean_ref - obs["torso_angle"]) * 57.3 - kd * obs["torso_omega"] * 57.3
    hip = (B + L + fb + 60) / 67.5 - 1
    knee = (110 - K) / 55 - 1
    ank = (D + 50) / 55 - 1
    return [max(-1, min(1, hip)), max(-1, min(1, knee)), max(-1, min(1, hip)), max(-1, min(1, knee)), sh, sh,
            max(-1, min(1, ank)), max(-1, min(1, ank))]


def gait(obs):
    phase = obs['time'] * math.tau / STRIDE_PERIOD
    ramp = min(1, obs['time'] / 0.5)
    legs, ankles = [], []
    for side in (0, 1):
        leg_phase = phase + side * math.pi
        swing = ramp * HIP_SWING * math.cos(leg_phase) + LEG_OFFSET
        knee = KNEE_BASE - ramp * KNEE_SWING * max(0, -math.sin(leg_phase + KNEE_PHASE))
        hip = swing + HIP_OFFSET + BALANCE_GAIN * obs['torso_angle'] + BALANCE_DAMPING * obs['torso_omega']
        ankle = -swing - knee
        legs.extend([(hip * 180 / math.pi - 7.5) / 67.5, 1 + knee * 180 / math.pi / 55])
        ankles.append((ankle * 180 / math.pi - 5) / 55)
    return legs + [-0.39, -0.39] + ankles


S = {"phase": "run", "pt": 0.0, "jumped": False, "last_t": None}
TRIG = 0.68
CROUCH_T = 0.11
PH_LO = 0.0
PH_HI = 1.0


def act(obs):
    t = obs["time"]
    if S["last_t"] is None:
        S["last_t"] = t
    dt = min(max(t - S["last_t"], 0.0), 0.05)
    S["last_t"] = t
    air = not (obs["contact"]["foot_L"] or obs["contact"]["foot_R"])
    ph = S["phase"]
    d = obs["dist_to_line"]

    if not S["jumped"] and d < TRIG and not air:
        frac = (t / STRIDE_PERIOD) % 1.0
        if (PH_LO <= frac <= PH_HI and abs(obs["torso_angle"]) < 0.45 and obs["vx"] > 0.3) or d < 0.28:
            S["phase"] = "crouch"
            S["jumped"] = True
            S["pt"] = 0.0
            ph = "crouch"

    if ph == "run":
        a = gait(obs)
    elif ph == "crouch":
        S["pt"] += dt
        p = min(1.0, S["pt"] / CROUCH_T)
        a = pose_deg(20 * p, 42 * p, 55 * p, 15 + 10 * p, -0.4, lean_ref=20 * p, obs=obs)
        both = obs["contact"]["foot_L"] and obs["contact"]["foot_R"]
        if not air and S["pt"] >= CROUCH_T + 0.02 and (both or S["pt"] > 0.30):
            S["phase"] = "extend"
            S["pt"] = 0.0
        elif air and S["pt"] > 0.45:
            S["phase"] = "extend"
            S["pt"] = 0.0
    elif ph == "extend":
        S["pt"] += dt
        p = min(1.0, S["pt"] / 0.10)
        D = 20.0 if S["pt"] < 0.03 else -50.0
        a = pose_deg(14, 42 - 64 * p, 55 * (1 - p), D, 0.9, lean_ref=14, obs=obs)
        if air:
            S["phase"] = "fly"
            S["pt"] = 0.0
        elif S["pt"] > 0.5:
            S["phase"] = "crouch"
            S["pt"] = 0.0
    elif ph == "fly":
        S["pt"] += dt
        if S["pt"] < 0.25:
            a = pose_deg(18, 25, 60, 20, -0.5)
        elif S["pt"] < 0.45:
            a = pose_deg(26, 45, 35, 15, 0.0)
        else:
            a = pose_deg(32, 58, 12, 8, 0.6)
        if not air:
            S["phase"] = "land"
            S["pt"] = 0.0
    else:
        S["pt"] += dt
        a = pose_deg(48, 55, 12, 5, 1.0)
    return [max(-1.0, min(1.0, v)) for v in a]
