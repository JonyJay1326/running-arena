"""--body-style robot：机器人小人（纯渲染，不碰物理/计分/随机数）。

接口与 viz_runner / viz_body2 的 draw_runner 完全相同：
    head = draw_runner(surf, v, pose, color, expr, k, lane_i=i, label="R", look=(vx, vy))
颜色全部来自 viz_runner.palette（主题色 = 外壳漆色，点缀色 = 灯带/关节环），所以同屏拉开颜色的逻辑不变。

两种风格（模块变量 STYLE，对比用）：
  cute       Q 版圆润小机器人：圆锥形四肢 + 球关节，外壳主色、小腿/前臂金属色，圆角显示屏头 + LED 表情 + 天线，胸口号码牌 + 能量灯
  industrial 写实工业风（类 Atlas / Unitree）：深灰骨架 + 主色装甲片，外露活塞杆 / 关节轮毂，头部是一条发光面罩，背后电池包

头部是"显示屏"：原本贴头像的位置（head 返回值）不变，--avatars 时头像贴在屏幕上（后续再做屏幕裁剪）。
"""
import math

import pygame
import pygame.gfxdraw as gfx

import physics
import viz_body2 as b2
import viz_fx
import viz_runner as vr
from viz_fx import mix, pf, px, shade, text

PPM = physics.PPM
TW, TH = physics.TORSO_W, physics.TORSO_H
HR = physics.HEAD_R
THW, THH = physics.THIGH_W, physics.THIGH_H
SHW, SHH = physics.SHIN_W, physics.SHIN_H
AW, AH = physics.ARM_W, physics.ARM_H
FW, FH = physics.FORE_W, physics.FORE_H

STYLE = "cute"

METAL_DK = (52, 58, 72)
METAL = (118, 127, 144)
METAL_HI = (184, 192, 206)
STEEL = (86, 94, 110)
GLOW = (110, 236, 255)
SCREEN = (14, 22, 42)
RUBBER = (40, 42, 52)


# ------------------------------------------------------------ 调色板 ----
_pal3 = {}


def palette(color, far, lane_i):
    P = vr.palette(color, far, lane_i)
    q = _pal3.get(id(P))
    if q is None or q[0] is not P:
        Q = dict(P)
        acc = P["band"]
        Q["acc"] = (250, 250, 252) if viz_fx.color_dist(acc, P["jersey"]) < 90 else acc
        Q["shell"], Q["shell_lo"], Q["shell_hi"] = P["jersey"], P["jersey_lo"], P["jersey_hi2"]
        tint = (lambda c: vr._far(c)) if far else (lambda c: c)
        Q["m_dk"], Q["m"], Q["m_hi"], Q["steel"] = tint(METAL_DK), tint(METAL), tint(METAL_HI), tint(STEEL)
        Q["m_lo"] = shade(Q["m"], 0.78)
        Q["glow"], Q["screen"], Q["rubber"] = GLOW, SCREEN, tint(RUBBER)
        if far:
            Q["glow"] = mix(GLOW, (60, 80, 110), 0.35)
        q = _pal3[id(P)] = (P, Q)
    return q[1]


# ------------------------------------------------------------ 原语 ----
_off3 = {}


def _shape(surf, f, pts, fill, out, ow, kk, aa=False):
    """局部多边形（物理 px）+ 外扩描边。"""
    if ow:
        key = (id(pts), round(ow / kk, 2))
        r = _off3.get(key)
        if r is None:
            area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]))
            src = pts if area > 0 else list(reversed(pts))
            r = _off3[key] = (pts, vr._offset(src, ow / kk))
        vr._poly(surf, out, [f(x, y) for x, y in r[1]], ow >= 2)
    vr._poly(surf, fill, [f(x, y) for x, y in pts])


def _rr(x0, y0, x1, y1, r_top=2.0, r_bot=2.0):
    return vr._rrect(x0, y0, x1, y1, r_top, r_bot, 2)


def _chamfer(x0, y0, x1, y1, c=3.0):
    return [(x0 + c, y0), (x1 - c, y0), (x1, y0 + c), (x1, y1 - c), (x1 - c, y1), (x0 + c, y1), (x0, y1 - c), (x0, y0 + c)]


def _bolt(surf, c, r, Q, ow, detail, ring=None):
    """关节球 / 轮毂：深色底 + 亮色环 + 中心螺丝。"""
    ring = ring or Q["m"]
    if ow:
        pygame.draw.circle(surf, Q["out"], c, r + ow)
    pygame.draw.circle(surf, Q["m_dk"], c, r)
    if r >= pf(3.2):
        pygame.draw.circle(surf, ring, c, r * 0.72)
        pygame.draw.circle(surf, Q["m_dk"], c, r * 0.36)
        if detail and r >= pf(4.5):
            pygame.draw.circle(surf, Q["acc"], c, max(1, r * 0.15))


_cache_rot = {}


# ================================================================ cute ====
def _leg_cute(surf, ft, fs, fsh, Q, ow, kk, detail, cel):
    hip, knee = ft(0, THH / 2), ft(0, -THH / 2)
    k2, ank = fs(0, SHH / 2), fs(0, -SHH / 2)
    km = b2._lerp(knee, k2, 0.5)
    aa = ow >= 2
    rt0, rt1 = THW / 2 * 1.02 * kk, THW / 2 * 0.80 * kk
    rs0, rs1 = SHW / 2 * 0.95 * kk, SHW / 2 * 0.66 * kk
    out = Q["out"]
    if ow:
        b2._caps(surf, out, hip, km, rt0, rt1, ow, aa)
        b2._caps(surf, out, km, ank, rs0, rs1, ow, aa)
        vr._poly(surf, out, [fsh(x, y) for x, y in vr._off("shoe", vr.SHOE, ow / kk)], aa)
    # 小腿：金属
    b2._shaded(surf, km, ank, rs0, rs1, Q["m_lo"], Q["m"], cel)
    if detail:
        b2._hl(surf, km, ank, rs0, rs1, Q["m_hi"], 0.15, 0.55)
    # 小腿上的点缀色环（脚踝上方）
    if rs1 >= pf(2.4):
        a_, b_ = fs(0, -SHH / 2 + 13), fs(0, -SHH / 2 + 9)
        r_ = rs1 + 0.5 * kk
        if ow:
            b2._caps(surf, out, a_, b_, r_, r_, max(1, ow - 1), cap_a=False, cap_b=False)
        b2._caps(surf, Q["acc"], a_, b_, r_, r_, cap_a=False, cap_b=False)
    # 大腿：外壳
    b2._shaded(surf, hip, km, rt0, rt1, Q["shell_lo"], Q["shell"], cel)
    if detail:
        b2._hl(surf, hip, km, rt0, rt1, Q["shell_hi"], 0.18, 0.78)
    # 脚
    _foot_cute(surf, fsh, Q, ow, kk, detail)
    # 膝球、髋球
    _bolt(surf, km, max(pf(3.2), 5.0 * kk), Q, ow if ow else 0, detail)
    _bolt(surf, hip, max(pf(3.4), 5.6 * kk), Q, ow if ow else 0, detail)


def _foot_cute(surf, f, Q, ow, kk, detail):
    fb, x0, x1 = vr._FOOT_BOT, vr._FOOT_X0, vr._FOOT_X1
    vr._poly(surf, Q["shell"], [f(x, y) for x, y in vr.SHOE])
    vr._poly(surf, Q["shell_lo"], [f(x, min(y, fb + 5.0)) for x, y in vr.SHOE])
    sole_t = vr._SOLE_TOP + 0.9
    vr._poly(surf, Q["rubber"], [f(x, min(y, sole_t)) for x, y in vr.SHOE])
    if kk * 3 >= pf(1.6):                                         # 点缀色脚背灯带
        pygame.draw.line(surf, Q["acc"], f(x0 + 7, fb + 6.2), f(x1 - 5, fb + 5.6), max(1, int(kk * 1.8)))
    if detail:
        pygame.draw.circle(surf, Q["m_hi"], f(x1 - 7, fb + 4.4), max(1, int(kk * 1.4)))   # 脚面螺丝


def _arm_cute(surf, fa, ff, Q, ow, kk, detail, cel):
    sh, el = fa(0, AH / 2), fa(0, -AH / 2)
    e2, wr = ff(0, FH / 2), ff(0, -FH / 2 + 4.5)
    em = b2._lerp(el, e2, 0.5)
    hand = ff(0, -FH / 2 + 3.6)
    ra0, ra1 = AW / 2 * 0.95 * kk, AW / 2 * 0.74 * kk
    rf0, rf1 = FW / 2 * 0.98 * kk, FW / 2 * 0.7 * kk
    aa = ow >= 2
    if ow:
        b2._caps(surf, Q["out"], sh, em, ra0, ra1, ow, aa)
        b2._caps(surf, Q["out"], em, wr, rf0, rf1, ow, aa)
        pygame.draw.circle(surf, Q["out"], hand, 4.6 * kk + ow)
    b2._shaded(surf, em, wr, rf0, rf1, Q["m_lo"], Q["m"], cel)
    b2._shaded(surf, sh, em, ra0, ra1, Q["shell_lo"], Q["shell"], cel)
    # 手：球形夹爪（深色球 + 开口）
    hr = 4.4 * kk
    pygame.draw.circle(surf, Q["m_dk"], hand, hr)
    if hr >= pf(3):
        pygame.draw.circle(surf, Q["steel"], (hand[0] - hr * 0.2, hand[1] - hr * 0.25), hr * 0.55)
    if detail:
        b2._hl(surf, sh, em, ra0, ra1, Q["shell_hi"], 0.15, 0.7)
    _bolt(surf, em, max(pf(2.8), 4.2 * kk), Q, ow if ow else 0, detail)
    _bolt(surf, sh, max(pf(3.2), 5.0 * kk), Q, ow if ow else 0, detail, ring=Q["acc"])


_TORSO_C = _rr(-TW / 2 - 0.5, -TH / 2, TW / 2 + 0.5, TH / 2 - 1, 9, 6)
_PELVIS = _rr(-TW / 2 - 0.2, -TH / 2 - 0.4, TW / 2 + 0.2, -TH / 2 + 11, 1.5, 5)
_CHEST = _rr(-6.2, -TH / 2 + 24, 6.8, TH / 2 - 7, 3, 3)
_BAT_C = _rr(-TW / 2 - 6, -TH / 2 + 20, -TW / 2 + 1, TH / 2 - 14, 2, 2)


def _torso_cute(surf, f, a, Q, ow, kk, detail, label, cel):
    aa = ow >= 2
    # 背后电池包
    _shape(surf, f, _BAT_C, Q["m"], Q["out"], ow, kk, aa)
    if detail:
        _shape(surf, f, _rr(-TW / 2 - 4.4, -TH / 2 + 26, -TW / 2 - 1.2, -TH / 2 + 30, 1, 1), Q["acc"], Q["out"], 0, kk)
    vr._seg(surf, f(0, TH / 2 - 3), f(0, TH / 2 + 4), 3.2 * kk, Q["m_dk"], Q["out"], ow)       # 脖子
    _shape(surf, f, _TORSO_C, Q["shell"], Q["out"], ow, kk, aa)
    lit = 1 if (-0.6 * math.cos(a) + 0.8 * math.sin(a)) > 0 else -1
    if cel:
        band = b2._clip_half(_TORSO_C, -lit, TW / 2 - 3.6)
        if len(band) >= 3:
            vr._poly(surf, Q["shell_lo"], [f(x, y) for x, y in band])
    # 骨盆（金属）+ 点缀色腰线
    _shape(surf, f, _PELVIS, Q["m_dk"], Q["out"], ow, kk, aa)
    pygame.draw.line(surf, Q["acc"], f(-TW / 2, -TH / 2 + 12.4), f(TW / 2, -TH / 2 + 12.4), max(1, int(kk * 1.4)))
    # 胸前面板：深色屏 + 号码牌 / 能量灯
    if kk * 2 >= pf(1.2):
        _shape(surf, f, _CHEST, Q["screen"], Q["out"], max(0, ow - 1), kk)
        cx, cy = f(0.3, -TH / 2 + 38)
        if detail and label:
            size = int(11 * kk)
            while size > px(7) and text(label, size, Q["glow"], True).get_width() > 10.5 * kk:
                size -= 1
            img = vr._bib_text(label, size, Q["glow"], int(round(math.degrees(a) / 4.0)) * 4 % 360)
            surf.blit(img, (cx - img.get_width() / 2, cy - img.get_height() / 2))
        else:
            pygame.draw.circle(surf, Q["glow"], (cx, cy), max(1.5, 3.2 * kk))
        for j in range(3):                     # 面板下沿三颗指示灯
            pygame.draw.circle(surf, (Q["acc"], Q["glow"], Q["m_hi"])[j], f(-3.2 + j * 3.6, -TH / 2 + 28), max(1, 0.95 * kk))


def _head_cute(surf, c, R, a, Q, ow, expr, k, look):
    ca, sa = math.cos(a), math.sin(a)

    def L(lx, ly):
        return (c[0] + (lx * ca - ly * sa) * R, c[1] - (lx * sa + ly * ca) * R)

    def rr(cx, cy, w, h, ch=0.12):
        return [L(cx - w / 2 + ch, cy - h / 2), L(cx + w / 2 - ch, cy - h / 2), L(cx + w / 2, cy - h / 2 + ch), L(cx + w / 2, cy + h / 2 - ch),
                L(cx + w / 2 - ch, cy + h / 2), L(cx - w / 2 + ch, cy + h / 2), L(cx - w / 2, cy + h / 2 - ch), L(cx - w / 2, cy - h / 2 + ch)]

    aa = ow >= 2
    # 天线
    if R >= pf(5):
        p0, p1 = L(-0.15, 0.85), L(-0.3, 1.45)
        pygame.draw.line(surf, Q["out"], p0, p1, max(2, int(R * 0.12)) + ow)
        pygame.draw.line(surf, Q["m_hi"], p0, p1, max(1, int(R * 0.1)))
        pygame.draw.circle(surf, Q["out"], p1, R * 0.22 + ow)
        pygame.draw.circle(surf, Q["acc"], p1, R * 0.22)
    # 耳盘
    if R >= pf(5):
        e = L(-0.9, -0.02)
        pygame.draw.circle(surf, Q["out"], e, R * 0.3 + ow)
        pygame.draw.circle(surf, Q["shell"], e, R * 0.3)
    # 外壳 + 屏幕
    body = rr(0.12, 0, 2.4, 1.95, 0.45)
    if ow:
        pygame.draw.polygon(surf, Q["out"], body, ow * 2)
    vr._poly(surf, Q["shell"], body)
    if R >= pf(5):
        scr = rr(0.2, 0.0, 2.0, 1.5, 0.34)
        vr._poly(surf, Q["m_dk"], rr(0.2, 0.0, 2.1, 1.6, 0.36))
        vr._poly(surf, Q["screen"], scr)
        pygame.draw.line(surf, mix(Q["screen"], (255, 255, 255), 0.18), L(-0.55, 0.62), L(0.1, 0.62), max(1, int(R * 0.08)))
    else:
        vr._poly(surf, Q["screen"], rr(0.2, 0, 1.9, 1.35, 0.3))
    _face_cute(surf, L, R, expr, k, look, Q)


def _face_cute(surf, L, R, expr, k, look, Q):
    g = Q["glow"]
    lw = max(1, int(R * 0.13))
    if R < pf(3.5):
        pygame.draw.circle(surf, g, L(0.6, 0.1), px(1))
        return
    lx, ly = look
    o = 0.05
    ex = (0.0 + lx * o, 0.55 + lx * o)
    ey = 0.1 + ly * o
    if expr == "dizzy":
        for j, cx in enumerate(ex):
            pts = []
            for q in range(14):
                tt = q / 13 * 4 * math.pi
                rr_ = R * (0.04 + 0.2 * q / 13)
                aa = tt + k * 0.25 * (1 if j else -1)
                p = L(cx, ey)
                pts.append((p[0] + math.cos(aa) * rr_, p[1] + math.sin(aa) * rr_))
            pygame.draw.lines(surf, g, False, pts, max(1, px(1)))
        pygame.draw.lines(surf, g, False, [L(0.0 + 0.1 * q, -0.4 + (0.06 if q % 2 else -0.04)) for q in range(6)], lw)
        return
    if expr == "grin":
        for cx in ex:
            pygame.draw.lines(surf, g, False, [L(cx - 0.17, 0.0), L(cx, 0.2), L(cx + 0.17, 0.0)], lw + 1)
        pts = [L(0.28 + 0.4 * math.cos(t), -0.18 + 0.28 * math.sin(t)) for t in [math.pi + q * math.pi / 8 for q in range(9)]]
        pygame.draw.polygon(surf, g, pts)
        return
    big = expr == "surprised"
    w, h = (0.4, 0.5) if big else (0.28, 0.42)
    for cx in ex:
        p = L(cx, ey)
        if big:
            pygame.draw.circle(surf, g, p, R * 0.27)
            pygame.draw.circle(surf, Q["screen"], p, R * 0.12)
        else:
            pygame.draw.rect(surf, g, pygame.Rect(p[0] - R * w / 2, p[1] - R * h / 2, R * w, R * h), border_radius=max(1, int(R * 0.1)))
    if big:
        pygame.draw.circle(surf, g, L(0.28, -0.42), max(1, R * 0.15), max(1, int(R * 0.07)))
    else:
        pygame.draw.lines(surf, g, False, [L(0.1, -0.4), L(0.28, -0.5), L(0.46, -0.4)], lw)


# ========================================================= industrial ====
def _piston(surf, a, b, Q, ow, kk, w=1.3):
    """外露活塞：细杆 + 一段粗缸体。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    d = math.hypot(dx, dy)
    if d < 3:
        return
    m = b2._lerp
    rod_a, rod_b = m(a, b, 0.0), m(a, b, 1.0)
    pygame.draw.line(surf, Q["out"], rod_a, rod_b, max(1, int(w * kk)) + 2 * ow)
    pygame.draw.line(surf, Q["m_hi"], rod_a, rod_b, max(1, int(w * kk)))
    c0, c1 = m(a, b, 0.05), m(a, b, 0.55)
    pygame.draw.line(surf, Q["out"], c0, c1, max(2, int(w * 2.8 * kk)) + 2 * ow)
    pygame.draw.line(surf, Q["steel"], c0, c1, max(2, int(w * 2.8 * kk)))


_PLATE_T = _chamfer(-1.0, -17.0, 7.6, 20.0, 3.4)
_PLATE_S = [(-0.6, -19.0), (6.4, -19.0), (6.0, 14.0), (3.0, 20.0), (-0.6, 20.0)]
_PLATE_ARM = _chamfer(-4.6, -11.0, 5.0, 14.0, 2.6)
_PLATE_FORE = _chamfer(-3.6, -13.0, 4.4, 15.0, 2.2)


def _leg_ind(surf, ft, fs, fsh, Q, ow, kk, detail, cel):
    hip, knee = ft(0, THH / 2), ft(0, -THH / 2)
    k2, ank = fs(0, SHH / 2), fs(0, -SHH / 2)
    km = b2._lerp(knee, k2, 0.5)
    aa = ow >= 2
    rt, rs = 4.1 * kk, 3.6 * kk
    if ow:
        b2._caps(surf, Q["out"], hip, km, rt, rt, ow, aa)
        b2._caps(surf, Q["out"], km, ank, rs, rs, ow, aa)
        vr._poly(surf, Q["out"], [fsh(x, y) for x, y in vr._off("shoe", vr.SHOE, ow / kk)], aa)
    # 骨架
    b2._caps(surf, Q["m_dk"], hip, km, rt, rt)
    b2._caps(surf, Q["m_dk"], km, ank, rs, rs)
    # 活塞（在骨架后侧，躯干后方）
    if kk * 2 >= pf(1.5):
        bk = (-4.6, 0)
        _piston(surf, ft(bk[0], THH / 2 - 8), ft(bk[0] * 0.8, -THH / 2 + 4), Q, 0, kk)
        _piston(surf, fs(-4.4, SHH / 2 - 4), fs(-4.0, -SHH / 2 + 8), Q, 0, kk, 1.1)
    # 装甲片（主色）
    _shape(surf, ft, _PLATE_T, Q["shell"], Q["out"], ow, kk)
    _shape(surf, fs, _PLATE_S, Q["shell"], Q["out"], ow, kk)
    pygame.draw.line(surf, Q["acc"], ft(0.4, 4.0), ft(6.6, 4.0), max(1, int(kk * 1.4)))
    pygame.draw.line(surf, Q["acc"], fs(0.2, -4.0), fs(5.8, -4.0), max(1, int(kk * 1.4)))
    _foot_ind(surf, fsh, Q, ow, kk, detail)
    _bolt(surf, km, max(pf(3.4), 5.4 * kk), Q, ow, detail, ring=Q["steel"])
    _bolt(surf, hip, max(pf(3.8), 6.0 * kk), Q, ow, detail, ring=Q["steel"])
    _bolt(surf, ank, max(pf(2.4), 3.6 * kk), Q, ow, detail, ring=Q["m"])


def _foot_ind(surf, f, Q, ow, kk, detail):
    fb, x0, x1 = vr._FOOT_BOT, vr._FOOT_X0, vr._FOOT_X1
    vr._poly(surf, Q["m_dk"], [f(x, y) for x, y in vr.SHOE])
    vr._poly(surf, Q["shell"], [f(x, y) for x, y in [(x0 + 3, fb + 3.0), (x1 - 1, fb + 3.0), (x1 + 0.2, fb + 4.8), (x1 - 4.0, fb + 6.0), (3.4, fb + 8.0), (1.0, fb + 6.8), (x0 + 3, fb + 5.4)]])
    sole_t = vr._SOLE_TOP + 0.9
    vr._poly(surf, Q["rubber"], [f(x, min(y, sole_t)) for x, y in vr.SHOE])
    if detail:
        for j in range(4):
            x = x0 + 5 + j * (x1 - x0 - 10) / 3
            pygame.draw.line(surf, Q["m"], f(x, fb + 0.5), f(x, sole_t - 0.5), 1)


def _arm_ind(surf, fa, ff, Q, ow, kk, detail, cel):
    sh, el = fa(0, AH / 2), fa(0, -AH / 2)
    e2, wr = ff(0, FH / 2), ff(0, -FH / 2 + 5)
    em = b2._lerp(el, e2, 0.5)
    hand = ff(0, -FH / 2 + 3.8)
    ra, rf = 3.4 * kk, 3.0 * kk
    aa = ow >= 2
    if ow:
        b2._caps(surf, Q["out"], sh, em, ra, ra, ow, aa)
        b2._caps(surf, Q["out"], em, wr, rf, rf, ow, aa)
        pygame.draw.circle(surf, Q["out"], hand, 4.2 * kk + ow)
    b2._caps(surf, Q["m_dk"], sh, em, ra, ra)
    b2._caps(surf, Q["m_dk"], em, wr, rf, rf)
    _shape(surf, fa, _PLATE_ARM, Q["shell"], Q["out"], ow, kk)
    _shape(surf, ff, _PLATE_FORE, Q["m"], Q["out"], ow, kk)
    # 夹爪：两瓣
    hr = 4.2 * kk
    pygame.draw.circle(surf, Q["m_dk"], hand, hr)
    if hr >= pf(3):
        pygame.draw.circle(surf, Q["m"], (hand[0] - hr * 0.18, hand[1] - hr * 0.2), hr * 0.5)
    pygame.draw.line(surf, Q["acc"], fa(-4.0, -1.0), fa(4.4, -1.0), max(1, int(kk * 1.3)))
    _bolt(surf, em, max(pf(2.8), 4.0 * kk), Q, ow, detail, ring=Q["steel"])
    _bolt(surf, sh, max(pf(3.4), 5.2 * kk), Q, ow, detail, ring=Q["steel"])


_TORSO_I = _chamfer(-TW / 2 - 0.4, -TH / 2 + 8, TW / 2 + 0.6, TH / 2, 5.0)
_CHEST_I = _chamfer(-TW / 2 + 0.6, -TH / 2 + 22, TW / 2 + 0.2, TH / 2 - 1.5, 3.4)
_PELVIS_I = _chamfer(-TW / 2 - 0.4, -TH / 2 - 1.0, TW / 2 + 0.6, -TH / 2 + 10, 2.4)
_PACK_I = _chamfer(-TW / 2 - 8.0, -TH / 2 + 16, -TW / 2 + 1.0, TH / 2 - 8, 2.2)


def _torso_ind(surf, f, a, Q, ow, kk, detail, label, cel):
    aa = ow >= 2
    _shape(surf, f, _PACK_I, Q["m"], Q["out"], ow, kk, aa)                    # 背后电池包
    if detail:
        for j in range(3):
            pygame.draw.line(surf, Q["m_dk"], f(-TW / 2 - 6.4, -TH / 2 + 22 + j * 5), f(-TW / 2 - 1.6, -TH / 2 + 22 + j * 5), 1)
    vr._seg(surf, f(0, TH / 2 - 4), f(0, TH / 2 + 4), 3.0 * kk, Q["m_dk"], Q["out"], ow)
    _shape(surf, f, _TORSO_I, Q["m_dk"], Q["out"], ow, kk, aa)                # 躯干骨架
    _shape(surf, f, _CHEST_I, Q["shell"], Q["out"], ow, kk, aa)               # 胸甲
    lit = 1 if (-0.6 * math.cos(a) + 0.8 * math.sin(a)) > 0 else -1
    if cel:
        band = b2._clip_half(_CHEST_I, -lit, TW / 2 - 3.4)
        if len(band) >= 3:
            vr._poly(surf, Q["shell_lo"], [f(x, y) for x, y in band])
    if kk * 2 >= pf(1.2):
        pygame.draw.line(surf, Q["acc"], f(-TW / 2 + 0.6, -TH / 2 + 30), f(TW / 2 + 0.2, -TH / 2 + 30), max(1, int(kk * 1.6)))
    if detail:
        for j in range(3):                                                     # 腹部散热栅
            pygame.draw.line(surf, Q["m"], f(-4.5, -TH / 2 + 12 + j * 3), f(4.5, -TH / 2 + 12 + j * 3), max(1, int(kk * 1.0)))
        if label:
            size = int(11 * kk)
            while size > px(7) and text(label, size, Q["screen"], True).get_width() > 11 * kk:
                size -= 1
            img = vr._bib_text(label, size, Q["screen"] if viz_fx.color_dist(Q["shell"], (255, 255, 255)) > 200 else (255, 255, 255),
                               int(round(math.degrees(a) / 4.0)) * 4 % 360)
            cx, cy = f(0.4, -TH / 2 + 40)
            surf.blit(img, (cx - img.get_width() / 2, cy - img.get_height() / 2))
    _shape(surf, f, _PELVIS_I, Q["m_dk"], Q["out"], ow, kk, aa)
    if detail:
        pygame.draw.circle(surf, Q["glow"], f(0.4, -TH / 2 + 4.5), max(1, 1.5 * kk))


def _head_ind(surf, c, R, a, Q, ow, expr, k, look):
    ca, sa = math.cos(a), math.sin(a)

    def L(lx, ly):
        return (c[0] + (lx * ca - ly * sa) * R, c[1] - (lx * sa + ly * ca) * R)

    def poly(pts):
        return [L(x, y) for x, y in pts]

    aa = ow >= 2
    shell = _chamfer(-0.95, -0.85, 1.25, 0.95, 0.38)
    if R >= pf(5):                                           # 顶部传感器
        vr._poly(surf, Q["out"], poly(_chamfer(-0.35, 0.85, 0.35, 1.3, 0.12)), aa)
        vr._poly(surf, Q["m"], poly(_chamfer(-0.3, 0.88, 0.3, 1.2, 0.1)))
        pygame.draw.circle(surf, Q["acc"], L(0.0, 1.22), max(1, R * 0.09))
    if ow:
        pygame.draw.polygon(surf, Q["out"], poly(shell), ow * 2)
    vr._poly(surf, Q["m_dk"], poly(shell))
    vr._poly(surf, Q["shell"], poly([(-0.95, 0.34), (-0.57, 0.95), (0.87, 0.95), (1.25, 0.57), (1.25, 0.34)]))
    # 面罩（发光）
    vr._poly(surf, Q["screen"], poly(_chamfer(-0.2, -0.46, 1.1, 0.24, 0.16)))
    lx, ly = look
    g = Q["glow"]
    if R < pf(3.5):
        pygame.draw.line(surf, g, L(0.1, -0.1), L(0.9, -0.1), px(1))
        return
    sh = (lx * 0.05, ly * 0.04)
    if expr == "dizzy":
        for j, cx in enumerate((0.15, 0.78)):
            pts = []
            for q in range(12):
                tt = q / 11 * 3.5 * math.pi
                rr_ = R * (0.03 + 0.16 * q / 11)
                aa_ = tt + k * 0.25 * (1 if j else -1)
                p = L(cx, -0.11)
                pts.append((p[0] + math.cos(aa_) * rr_, p[1] + math.sin(aa_) * rr_))
            pygame.draw.lines(surf, g, False, pts, 1)
    elif expr == "surprised":
        for cx in (0.2, 0.78):
            pygame.draw.circle(surf, g, L(cx + sh[0], -0.11 + sh[1]), R * 0.2)
            pygame.draw.circle(surf, Q["screen"], L(cx + sh[0], -0.11 + sh[1]), R * 0.09)
    elif expr == "grin":
        pygame.draw.lines(surf, g, False, [L(0.0, -0.3), L(0.2, -0.05), L(0.4, -0.3)], max(1, int(R * 0.12)))
        pygame.draw.lines(surf, g, False, [L(0.55, -0.3), L(0.75, -0.05), L(0.95, -0.3)], max(1, int(R * 0.12)))
    else:                                                    # 一条光带 + 两个亮点
        pygame.draw.line(surf, g, L(0.0 + sh[0], -0.11), L(1.0 + sh[0], -0.11), max(2, int(R * 0.28)))
        pygame.draw.line(surf, (255, 255, 255), L(0.1 + sh[0], -0.04), L(0.5 + sh[0], -0.04), max(1, int(R * 0.07)))


# ------------------------------------------------------------ 入口 --------
def draw_runner(surf, v, pose, color, expr, k, lane_i=0, label="", look=None):
    kk = v.scale / PPM
    ow = vr.outline_width(v.scale) if viz_fx.S == 1.0 else px(vr.outline_width(v.scale / viz_fx.S))
    detail = v.scale >= pf(80)
    cel = v.scale >= 70
    cute = STYLE == "cute"
    leg, arm, torso, head_f = ((_leg_cute, _arm_cute, _torso_cute, _head_cute) if cute
                               else (_leg_ind, _arm_ind, _torso_ind, _head_ind))
    head = None
    a0 = pose[0][2]
    fx_, fy_ = math.cos(a0), math.sin(a0)
    if look is not None:
        fx_, fy_ = fx_ * 0.8 + look[0] * 0.5, fy_ * 0.8 + look[1] * 0.5
    ln = math.hypot(fx_, fy_) or 1.0
    look_s = (fx_ / ln, -fy_ / ln)
    for b, far in vr.LAYERS:
        Q = palette(color, far, lane_i)
        if b == 0:
            f = vr._xf(v, pose[0])
            torso(surf, f, a0, Q, ow, kk, detail, label, cel)
            c = f(0, TH / 2 + HR)
            R = HR * kk
            head_f(surf, c, R, a0, Q, ow, expr, k, look_s)
            head = (c, R)
        elif b in (2, 4):
            foot = pose[vr.FOOT_IDX[0] if b == 2 else vr.FOOT_IDX[1]]
            leg(surf, vr._xf(v, pose[b - 1]), vr._xf(v, pose[b]), vr._xf(v, vr.shoe_pose(foot)), Q, ow, kk, detail, cel)
        elif b in (6, 8):
            arm(surf, vr._xf(v, pose[b - 1]), vr._xf(v, pose[b]), Q, ow, kk, detail, cel)
    return head
