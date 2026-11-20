import math

DEBUG = False

HL, HH = math.radians(-60), math.radians(75)
KL, KH = math.radians(-110), math.radians(-5)


def hu(rel):
    return max(-1.0, min(1.0, 2.0 * (rel - HL) / (HH - HL) - 1.0))


def ku(rel):
    return max(-1.0, min(1.0, 2.0 * (rel - KL) / (KH - KL) - 1.0))


def hrel(u):
    return HL + (u + 1.0) / 2.0 * (HH - HL)


def krel(u):
    return KL + (u + 1.0) / 2.0 * (KH - KL)


# ---- 参数 ----
A_V = 0.14
KV = 0.15
VD = 1.4
VD_RUN = 2.3
FF = 0.0
XG = 1.9
KTD_H = -0.65
KTD_F = -0.4
KTD_B = -0.4
BLEND = 0.0
FLATP = False
FVL = 2.5
FVL2 = 2.8
KTD_FL = -0.47
CAD = False
XLIM = 16.0
ARMS = False
ARM_U = -1.0
BUMPR = False
XS = 99.0
KTD_S = -0.6
NS = 999
VL1 = 2.6
VL2 = 2.8
EXP = False
EXPF = False
LEAN = 0.25      # 目标躯干前倾
KP_T = 0.8
KD_T = 0.08
TS = 0.30        # 名义步时
T_MIN = 0.22
T_MAX = 0.42
K_FLEX = -1.3    # 摆动屈膝峰值
K_TD = -0.40     # 落地时膝角
K_PUSH = -0.10   # 蹬伸末膝角

S = {'obst': None, 'st': 0, 't0': 0.0, 'n': 0, 'done': False, 'lastp': -1.0}


def act(obs):
    t = obs['time']
    th = obs['torso_angle']
    om = obs['torso_omega']
    vx = obs['vx']
    x = obs['x']
    j = obs['joints']
    c = obs['contact']
    hr = [hrel(j[0]), hrel(j[2])]
    kr = [krel(j[1]), krel(j[3])]
    foot = [c['foot_L'], c['foot_R']]
    if S['obst'] is None:
        S['obst'] = obs['next_bump_dist'] < 90 and not EXPF

    st = S['st']
    sw = 1 - st
    tau = t - S['t0']

    ts, tmin, tmax = TS, T_MIN, T_MAX
    if CAD and S['obst'] and x >= 13.2:
        ts, tmin, tmax = 0.285, 0.21, 0.40
    # 切换支撑腿
    if (tau > tmin and foot[sw]) or tau > tmax:
        S['st'] = sw
        S['t0'] = t
        S['n'] += 1
        st, sw = sw, st
        tau = 0.0
    s = min(1.0, tau / ts)

    # 支撑脚踝相对躯干中心的水平位置
    a_th = hr[st] - th
    a_sh = a_th + kr[st]
    ank_x = -0.30 * math.sin(th) + 0.45 * math.sin(a_th) + 0.45 * math.sin(a_sh)
    d = -ank_x   # 躯干在支撑脚前方的距离

    if (x > 18.3 or S['done']) and not EXP:
        S['done'] = True
        return [0.6, -0.6, 0.6, -0.6, -0.39, -0.39]

    lean, vd, poff, kflex = LEAN, VD, 0.0, K_FLEX
    ktd, up, down = (KTD_S if S['n'] >= NS else K_TD), False, False
    alim = -0.10
    if x > XG:
        lean = max(0.08, LEAN - 0.4 * (x - XG))
        vd = VD_RUN
        ktd = KTD_F
    if S['obst'] and not (EXP and x > 18.3):
        if FLATP and XG < x <= 5.0:
            if vx > 2.6:
                lean, poff = 0.0, 0.06
            else:
                ktd = -0.55
        if 5.0 < x <= 8.9:
            kflex = -1.6
            ktd = KTD_B
            if BUMPR:
                if vx > VL1:
                    lean = 0.0
                else:
                    ktd = -0.6
                if vx > VL2:
                    poff = 0.08
        elif 8.9 < x < 10.9:        # 上坡
            poff, kflex, lean, ktd, up = 0.0, -1.6, 0.25, -0.75, True
        elif 10.9 <= x < 13.2:    # 下坡
            lean, vd, poff, down = 0.0, 1.0, 0.15, True
            if x > 12.2:
                kflex = -1.6
        elif x >= 13.2:           # 跨栏
            lean, vd, poff, kflex = 0.12, 2.4, 0.0, -1.6
            ktd = KTD_H
            if x < XLIM:
                if vx > VL1:
                    ktd = K_TD
                    lean = 0.0
                if vx > VL2:
                    poff = 0.08
            if x < 14.0:
                poff += BLEND * (14.0 - x) / 0.8
            if x > XS:
                lean = 0.35
    elif x > XG:
        if vx > FVL:
            lean, poff = 0.0, 0.06
            if vx > FVL2:
                poff = 0.14
        else:
            ktd = KTD_FL

    phi = A_V * vx + max(alim, KV * (vx - vd)) + 0.25 * th + poff
    if x > XG:
        phi += 0.6 * max(0.0, th - 0.45)
    phi = max(-0.3, min(0.8, phi))
    out = [0.0] * 6
    # 摆动腿：大腿世界角 -> phi，限速
    e = max(-0.30, min(0.30, phi + th - hr[sw]))
    out[2 * sw] = hu(hr[sw] + e)
    kt = ktd + (kflex - ktd) * math.sin(math.pi * min(1.0, s / 0.85))
    ek = max(-0.5, min(0.5, kt - kr[sw]))
    out[2 * sw + 1] = ku(kr[sw] + ek)
    # 支撑腿：髋控制躯干姿态；膝先保持微屈，脚落到身后再蹬伸
    tgt = hr[st] - KP_T * (th - lean) - KD_T * om
    if x > XG:
        tgt -= FF * max(0.0, vx) / 18.0
    out[2 * st] = hu(tgt)
    kst = ktd if (s < 0.35 or d < 0.0) else K_PUSH
    if up:
        kst = ktd if s < 0.2 else K_PUSH
    if down:
        kst = -0.45
    ek = max(-0.4, min(0.4, kst - kr[st]))
    out[2 * st + 1] = ku(kr[st] + ek)
    out[4] = -0.39 + (0.4 if sw == 1 else -0.3)
    out[5] = -0.39 + (0.4 if sw == 0 else -0.3)
    if ARMS and x > XG:
        out[4] = ARM_U
        out[5] = ARM_U

    if DEBUG and t - S["lastp"] >= (1.0 if x > 17 else 9.0):
        S['lastp'] = t
        print("t=%.2f x=%.2f y=%.2f vx=%.2f th=%.2f om=%.2f st=%d tau=%.2f d=%.2f phi=%.2f f=%d%d n=%d j=%.2f %.2f %.2f %.2f" % (
            t, x, obs['y'], vx, th, om, st, tau, d, phi, foot[0], foot[1], S['n'], hr[0]-th, kr[0], hr[1]-th, kr[1]))
    return out
