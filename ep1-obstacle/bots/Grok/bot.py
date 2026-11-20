import math

S = {"prev_t": None, "rec_t": 0.0}


def clamp(v, lo=-1.0, hi=1.0):
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def norm_hip(rel):
    return clamp(2.0 * (rel - math.radians(-60.0)) / math.radians(135.0) - 1.0)


def wrapped(a):
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def fallen(obs):
    if obs["contact"]["torso"] or obs["y"] < 0.45:
        return True
    return abs(wrapped(obs["torso_angle"])) >= math.radians(50.0) and obs["y"] < 0.70


def recover(ang, rt):
    if ang < 0.10:
        if rt % 1.4 < 0.40:
            return [-0.05, -0.75, -0.05, -0.75, 0.35, 0.35]
        return [0.50, 0.95, 0.50, 0.95, 0.28, 0.28]
    if rt < 8.0:
        p = rt % 1.35
        if p < 0.42:
            return [0.95, -0.9, 0.95, -0.9, 0.15, 0.15]
        if p < 0.95:
            return [0.55, 1.0, 0.55, 1.0, -0.30, -0.30]
        return [0.08, 0.82, 0.08, 0.82, -0.12, -0.12]
    mode = int((rt - 8.0) / 1.7) % 4
    p = (rt - 8.0) % 1.7
    if mode == 0:
        return [0.78, 0.98, 0.78, 0.98, -0.30, -0.30]
    if mode == 1:
        s = math.sin(rt * 9.0)
        return [0.15 + 0.70 * s, -0.45, 0.15 - 0.70 * s, -0.45, 0.1, 0.1]
    if mode == 2:
        if p < 0.55:
            return [1.0, -1.0, 1.0, -1.0, 0.4, 0.4]
        return [-0.70, 1.0, -0.70, 1.0, -0.50, -0.50]
    if p < 0.7:
        return [0.95, -0.15, -0.75, 1.0, 0.2, 0.2]
    return [-0.15, 1.0, 0.70, -0.55, 0.0, 0.0]


def act(obs):
    t = obs["time"]
    ang = obs["torso_angle"]
    omega = obs["torso_omega"]
    vx = obs["vx"]
    x = obs["x"]
    y = obs["y"]
    prev = S["prev_t"]
    dt = 1.0 / 60.0 if prev is None else t - prev
    if dt <= 0.0 or dt > 0.1:
        dt = 1.0 / 60.0
    S["prev_t"] = t

    if fallen(obs):
        S["rec_t"] += dt
        if S["rec_t"] >= 8.0 and ang > 0.4:
            p = (S["rec_t"] - 8.0) % 2.2
            if p < 0.6:
                return [0.55, -0.35, 0.55, -0.35, 1.0, 1.0]
            if p < 1.4:
                return [-0.35, 1.0, -0.35, 1.0, 0.2, 0.2]
            return [0.2, 0.6, 0.2, 0.6, 0.4, 0.4]
        if S["rec_t"] < 8.0 or ang < 0.10:
            return recover(ang, S["rec_t"])
    else:
        S["rec_t"] = 0.0

    if x >= 18.06 and abs(vx) < 0.35 and y > 0.98 and abs(ang) < 0.35:
        hip = clamp(norm_hip(ang) - 0.8 * ang - 0.12 * omega)
        return [hip, 0.86, hip, 0.86, 0.1, 0.1]

    bd = obs["next_bump_dist"]
    bh = obs["next_bump_height"]
    course = bd < 25.0 or x > 13.5

    freq, amp, bend = 1.75, 0.32, 0.48
    lean = 0.06 + 0.012 * max(vx, 0.0)
    if course and 8.6 <= x < 11.15:
        lean, freq, amp, bend = 0.18, 1.45, 0.30, 0.60
    elif course and 11.15 <= x < 13.35:
        lean, freq, amp, bend = -0.05, 1.40, 0.24, 0.45
    if bd < 1.25 and bh >= 0.15:
        bend, freq, amp = 1.45, 1.35, 0.28
        lean = min(lean, 0.08)
    elif bd < 1.05 and bh >= 0.05:
        bend = max(bend, 0.95)
        freq = min(freq, 1.60)
    if x >= 18.06:
        amp, bend, freq, lean = 0.10, 0.12, 1.3, 0.0
    lean = min(lean, 0.24)

    ramp = clamp((t - 0.12) / 0.35, 0.0, 1.0)
    amp *= ramp
    bend *= ramp

    u_lo = -0.72 if ang > 0.50 else -0.34
    u = clamp(-1.05 * (ang - lean) - 0.28 * omega, u_lo, 0.34)
    w = 2.0 * math.pi * freq
    s = math.sin(w * t)
    c = math.cos(w * t)
    hL = u + amp * s
    hR = u - amp * s
    kL = 1.0 - bend * max(c, 0.0)
    kR = 1.0 - bend * max(-c, 0.0)

    if ang < lean - 0.08:
        if c < 0.0:
            hL = max(hL, u)
        else:
            hR = max(hR, u)

    reach = clamp(0.55 * (ang - 0.08), 0.0, 0.28)
    if c >= 0.0:
        hL += reach
        if reach > 0.04:
            kL = max(kL, 0.55)
    else:
        hR += reach
        if reach > 0.04:
            kR = max(kR, 0.55)

    if y < 1.04 and ang < 0.55:
        kL = max(kL, 0.95)
        kR = max(kR, 0.95)
    if S.get("rec_t", 0.0) >= 8.0 and y < 1.05:
        kL = max(kL, 0.72)
        kR = max(kR, 0.72)
        hL -= 0.08
        hR -= 0.08

    arm = clamp(-0.22 + 0.35 * (ang - lean), -0.65, 0.35)
    return [clamp(hL), clamp(kL), clamp(hR), clamp(kR), arm, arm]
