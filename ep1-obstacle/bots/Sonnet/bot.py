import math

TMIN = 0.25
TMAX = 0.4
VD = 0.5
KV = 0.4
TAU = 0.37
A0 = 0.45
KC = 2.0
KCW = 0.0
KI = 1.3
KW = 0.1
KA = 0.3
G_UT = 0.25
G_LEAN = 0.2
KSW_H = 0.0
SLOPE_L = 0.0
SLOPE_D = 0.25
KIV = 0.15
VDI = 0.8
VB = 1.7
KI2 = 1.5
CP_T = 0.10
SW_TIME = 0.3
KS = 0.95
KSW = 0.0
t0 = 0.8

S = {'sup': 0, 'ts': 0.0, 'h0': 0.0, 'ai': 0.0}
R135 = math.radians(135)
R60 = math.radians(60)


def hip_u(rel):
    return max(-1.0, min(1.0, 2.0 * (rel + R60) / R135 - 1.0))


def hip_rel(u):
    return (u + 1) / 2 * R135 - R60


def act(obs):
    t = obs['time']
    FLAT = obs['next_bump_dist'] > 90 and obs['x'] < 16.9
    ang = obs['torso_angle']
    j = obs['joints']
    vx = obs['vx']
    om = obs['torso_omega']
    c = obs['contact']
    if t < t0:
        sup = 2
        el = 0.0
        rels = hip_rel((j[0] + j[2]) / 2)
    else:
        if S['ts'] < t0:
            S['ts'] = t
            S['h0'] = j[2]
        sup = S['sup']
        el = t - S['ts']
        swing_foot = c['foot_L'] if sup == 1 else c['foot_R']
        if (el > TMIN and swing_foot) or el > (0.3 if obs['x'] > 8.4 else TMAX):
            S['sup'] = 1 - sup
            S['ts'] = t
            sup = S['sup']
            S['h0'] = j[2 * (1 - sup)]
            el = 0.0
        rels = hip_rel(j[2 * sup])
    if obs['x'] > 18.0 and (S.get('stand') or vx < 0.3):
        S['stand'] = True
        rs = hip_rel((j[0] + j[2]) / 2)
        cps = 0.9 * math.sin(ang - rs) + 0.3 * math.sin(ang) + 0.33 * vx
        hh = max(-1.0, min(1.0, -0.11 + 3.0 * (0.06 - cps)))
        return [hh, 1.0, hh, 1.0, -0.39, -0.39]
    L = ang - rels
    cp = 0.9 * math.sin(L) + 0.3 * math.sin(ang) + 0.33 * vx
    a_ref = A0 + (KC if sup == 2 else KCW) * (CP_T - cp)
    if sup != 2:
        a_ref += max(-0.3, min(0.15, KA * (VD - vx)))
    a_ref = max(-0.2, min(0.7, a_ref))
    if sup != 2:
        S['ai'] = max(-0.35, min(0.35, S['ai'] + KIV * (VDI - vx) / 60.0))
        if obs["x"] <= 7.6 and not FLAT:
            a_ref += S['ai']
        elif FLAT:
            a_ref += 0.5 * S['ai']
    careful = obs["x"] > 7.6 or FLAT
    POST = obs['x'] > 18.0
    if 9.0 < obs["x"] < 10.4:
        a_ref += SLOPE_L
    elif 10.4 <= obs["x"] < 13.0:
        a_ref -= SLOPE_D
    if obs["x"] > 14.8:
        a_ref -= G_LEAN
    vb = 1.1 if careful else VB
    if vx > vb and not careful:
        a_ref -= min(0.4, 0.4 * (vx - vb))
    if POST:
        a_ref = -0.1
    hs = hip_u(rels + KI * (a_ref - ang) - KW * om)
    if sup != 2:
        stf = c['foot_L'] if sup == 0 else c['foot_R']
        if not stf:
            hs = hip_u(rels + KI2 * (a_ref - ang) - KW * om)
    if sup == 2:
        return [hs, 1.0, hs, 1.0, -0.39, -0.39]
    d = TAU * vx + KV * (vx - (0.0 if POST else (VD if not careful else 0.5)))
    if POST:
        d = max(d, 0.3)
    if vx > vb:
        d += (0.8 if careful else 0.25) * (vx - vb)
    d = max(-0.15, min(0.3 if vx <= vb else 0.5, d))
    rel_t = math.asin(d / 0.95) + ang
    ut = hip_u(rel_t)
    if obs['x'] > 13.0:
        ut = min(ut, 0.6 if obs['x'] <= 14.8 else G_UT)
    s = min(1.0, el / SW_TIME)
    sh = S['h0'] + (ut - S['h0']) * (0.5 - 0.5 * math.cos(math.pi * s))
    if el < SW_TIME:
        ksw = KSW
        if obs['next_bump_height'] >= 0.15 and obs['next_bump_dist'] < 1.1:
            ksw = KSW_H
        sk = KS + (ksw - KS) * math.sin(math.pi * s)
    else:
        sk = KS
    if sup == 0:
        out = [hs, KS, sh, sk, -0.39, -0.39]
    else:
        out = [sh, sk, hs, KS, -0.39, -0.39]
    if False:
        print("t=%.2f x=%.2f y=%.2f ang=%.2f L=%.2f cp=%.2f vx=%.2f hs=%.2f sh=%.2f ut=%.2f sup=%d el=%.2f ct=%s" % (t, obs['x'], obs['y'], ang, L, cp, vx, hs, sh, ut, sup, el, ''.join('1' if v else '0' for v in c.values())))
    return out
