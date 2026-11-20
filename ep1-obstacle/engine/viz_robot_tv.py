"""A 版：复古铁皮发条玩具机器人（纯渲染，不碰物理/计分/随机数）。

接口同 viz_body2.draw_runner：draw_runner(surf, v, pose, color, expr, k, lane_i=0, label="", look=None) -> (头心, 头半径)

造型思路（50 年代日本铁皮玩具 + 8bit 像素屏）：
  - 头 = 老式显像管电视机：主题色铁皮机壳、银色圆角边框、深绿玻璃屏（大，留给头像），
    屏上是琥珀色"像素点阵"表情（9x7 点阵，normal / dizzy=X眼+屏幕信号撕裂 / surprised / grin），
    机壳后部两个调台旋钮，头顶 V 形兔耳天线（点缀色小球）
  - 躯干 = 铁皮罐：圆角、卷边、铆钉；胸口印刷花边带（点缀色 + 白圆点，模仿铁皮印刷）、
    圆形压力表（指针摆动）、两颗按钮、冲压号码牌；背后一把会转的发条钥匙（倒地背朝下时自动收起）
  - 大腿 / 上臂 = 银色波纹软管（一圈圈肋条）；小腿 = 下粗上细的主题色铁皮"靴筒"；前臂 = 铁皮护筒 + 钳形夹爪
  - 脚 = 方头铁皮靴：橡胶底 + 银色包头 + 侧面铆钉
缩放小（scale < 80，4 人 720p）时：去掉肋条/铆钉/压力表/印刷花纹/旋钮，像素脸简化成两个点。
"""
import math

import pygame

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
FB, FX0, FX1 = vr._FOOT_BOT, vr._FOOT_X0, vr._FOOT_X1

TIN = (176, 182, 192)          # 银色铁皮
TIN_HI = (232, 236, 242)
TIN_LO = (112, 118, 132)
TIN_DK = (70, 74, 88)
RUBBER = (44, 40, 46)
CRT = (22, 40, 34)             # 显像管玻璃
CRT_HI = (60, 92, 80)
AMBER = (255, 196, 70)         # 琥珀色荧光点阵
AMBER_DIM = (120, 90, 40)
GAUGE = (250, 246, 230)
BRASS = (222, 178, 74)


_palA = {}


def palette(color, far, lane_i):
    P = vr.palette(color, far, lane_i)
    q = _palA.get(id(P))
    if q is None or q[0] is not P:
        Q = dict(P)
        tint = (lambda c: vr._far(c)) if far else (lambda c: c)
        acc = P["band"]
        # 点缀色与主题色太近（默认赛道色时 band == jersey）→ 用奶油白，像铁皮印刷的留白
        Q["acc"] = tint((250, 240, 214)) if viz_fx.color_dist(acc, P["jersey"]) < 90 else acc
        Q["shell"], Q["shell_lo"], Q["shell_hi"] = P["jersey"], P["jersey_lo"], P["jersey_hi"]
        Q["shell_dk"] = shade(P["jersey"], 0.62)
        for n, c in (("tin", TIN), ("tin_hi", TIN_HI), ("tin_lo", TIN_LO), ("tin_dk", TIN_DK),
                     ("rubber", RUBBER), ("crt", CRT), ("crt_hi", CRT_HI), ("gauge", GAUGE), ("brass", BRASS)):
            Q[n] = tint(c)
        Q["amber"] = AMBER if not far else mix(AMBER, (90, 100, 120), 0.3)
        q = _palA[id(P)] = (P, Q)
    return q[1]


# ------------------------------------------------------------ 原语 ----
def _outlined(surf, pts, fill, out, ow):
    """任意（可凹）多边形 + 外描边：边线宽 2*ow 居中画 → 向外露出 ow。"""
    if ow:
        pygame.draw.polygon(surf, out, pts)
        pygame.draw.lines(surf, out, True, pts, 2 * ow)
        if ow >= 2:
            for p in pts[::2]:
                pygame.draw.circle(surf, out, p, ow)
    pygame.draw.polygon(surf, fill, pts)


def _rr(x0, y0, x1, y1, r):
    return vr._rrect(x0, y0, x1, y1, r, r, 2)


def _norm(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    d = math.hypot(dx, dy) or 1.0
    return dx / d, dy / d, d


def _rivet(surf, p, r, Q):
    pygame.draw.circle(surf, Q["tin_dk"], p, r)
    if r >= 1.5:
        pygame.draw.circle(surf, Q["tin_hi"], (p[0] - r * 0.3, p[1] - r * 0.3), r * 0.5)


def _hose(surf, a, b, r, Q, ow, kk, detail):
    """银色波纹软管：描边胶囊 + 一圈圈肋条。"""
    aa = ow >= 2
    if ow:
        b2._caps(surf, Q["out"], a, b, r, r, ow, aa)
    b2._caps(surf, Q["tin_lo"], a, b, r, r)
    ux, uy, d = _norm(a, b)
    nx, ny = -uy, ux
    if nx * -0.6 + ny * -0.8 < 0:
        nx, ny = -nx, -ny
    o = r * 0.18
    b2._caps(surf, Q["tin"], (a[0] + nx * o, a[1] + ny * o), (b[0] + nx * o, b[1] + ny * o), r * 0.78, r * 0.78)
    step = 3.4 * kk
    if step >= pf(2.6):
        n = int(d / step)
        lw = max(1, int(kk * 1.1))
        for i in range(1, n + 1):
            t = i * step
            cx, cy = a[0] + ux * t, a[1] + uy * t
            pygame.draw.line(surf, Q["tin_dk"], (cx + nx * r * 0.95, cy + ny * r * 0.95), (cx - nx * r * 0.95, cy - ny * r * 0.95), lw)
            if detail:
                c2 = (cx - ux * lw * 1.2, cy - uy * lw * 1.2)
                pygame.draw.line(surf, Q["tin_hi"], (c2[0] + nx * r * 0.7, c2[1] + ny * r * 0.7), (c2[0] + nx * r * 0.1, c2[1] + ny * r * 0.1), lw)
    elif r >= pf(2.2):
        for t in (0.33, 0.66):
            cx, cy = a[0] + ux * d * t, a[1] + uy * d * t
            pygame.draw.line(surf, Q["tin_dk"], (cx + nx * r, cy + ny * r), (cx - nx * r, cy - ny * r), 1)


def _tube(surf, a, b, ra, rb, Q, ow, kk, detail, cel, rim=True):
    """主题色铁皮筒：平口，两端银色卷边。"""
    aa = ow >= 2
    if ow:
        b2._caps(surf, Q["out"], a, b, ra, rb, ow, aa, cap_a=False, cap_b=False)
    b2._shaded(surf, a, b, ra, rb, Q["shell_lo"], Q["shell"], cel, cap_a=False, cap_b=False)
    if detail:
        b2._hl(surf, a, b, ra, rb, Q["shell_hi"], 0.2, 0.75)
    if rim and ra >= pf(2.4):
        ux, uy, d = _norm(a, b)
        w = min(2.0 * kk, d * 0.15)
        a2 = (a[0] + ux * w, a[1] + uy * w)
        b2._caps(surf, Q["tin"], a, a2, ra + 0.5 * kk, ra + 0.5 * kk, cap_a=False, cap_b=False)


# ------------------------------------------------------------ 腿 ----
BOOT = vr._chaikin([(FX0 - 0.8, FB), (FX1 + 1.2, FB), (FX1 + 1.8, FB + 3.5), (FX1 + 0.8, FB + 7.2),
                    (FX1 - 5.5, FB + 8.6), (SHW / 2 + 1.5, FB + 9.6), (SHW / 2 + 0.8, FB + 13.4),
                    (-SHW / 2 - 1.4, FB + 13.4), (FX0 - 0.2, FB + 10.5), (FX0 - 1.2, FB + 3.0)], 1)
SOLE_T = FB + 2.6
TOE = [(x, y) for x, y in BOOT if x > FX1 - 7.0]


def _boot(surf, f, Q, ow, kk, detail):
    pts = [f(x, y) for x, y in BOOT]
    _outlined(surf, pts, Q["shell"], Q["out"], ow)
    pygame.draw.polygon(surf, Q["shell_lo"], [f(x, min(y, FB + 5.0)) for x, y in BOOT])
    # 银色包头
    if kk * 3 >= pf(1.6):
        toe = [f(FX1 - 7.0, SOLE_T)] + [f(x, y) for x, y in TOE if y > SOLE_T] + [f(FX1 - 7.0, FB + 8.4)]
        if len(toe) >= 3:
            pygame.draw.polygon(surf, Q["tin"], toe)
            pygame.draw.line(surf, Q["tin_dk"], f(FX1 - 7.0, SOLE_T), f(FX1 - 7.0, FB + 8.4), max(1, int(kk)))
    # 靴口卷边
    pygame.draw.line(surf, Q["tin"], f(-SHW / 2 - 1.0, FB + 12.6), f(SHW / 2 + 0.6, FB + 12.6), max(1, int(kk * 1.8)))
    # 橡胶底
    pygame.draw.polygon(surf, Q["rubber"], [f(x, min(y, SOLE_T)) for x, y in BOOT])
    if detail:
        for j in range(3):
            _rivet(surf, f(FX0 + 4 + j * 4.5, FB + 6.0), max(1.0, 0.9 * kk), Q)
        for j in range(6):
            x = FX0 + 1.5 + j * (FX1 - FX0 - 2) / 5
            pygame.draw.line(surf, Q["tin_dk"], f(x, FB + 0.3), f(x + 1.2, SOLE_T - 0.4), 1)


def _leg(surf, ft, fs, fsh, Q, ow, kk, detail, cel):
    hip, knee = ft(0, THH / 2), ft(0, -THH / 2)
    k2, ank = fs(0, SHH / 2), fs(0, -SHH / 2)
    km = b2._lerp(knee, k2, 0.5)
    _hose(surf, hip, km, 4.4 * kk, Q, ow, kk, detail)
    # 小腿：上细下粗的靴筒，顶端在膝下一点
    top = fs(0, SHH / 2 - 5)
    _tube(surf, top, ank, 4.3 * kk, 5.6 * kk, Q, ow, kk, detail, cel)
    if detail:                                    # 靴筒上一条点缀色印刷带
        a_, b_ = fs(0, -2), fs(0, -6)
        b2._caps(surf, Q["acc"], a_, b_, 5.1 * kk, 5.2 * kk, cap_a=False, cap_b=False)
    elif kk * 4 >= pf(2):
        a_, b_ = fs(0, -2), fs(0, -5)
        b2._caps(surf, Q["acc"], a_, b_, 5.0 * kk, 5.0 * kk, cap_a=False, cap_b=False)
    _boot(surf, fsh, Q, ow, kk, detail)
    # 膝铰链 + 髋铰链
    _hinge(surf, km, max(pf(2.6), 3.6 * kk), Q, ow, detail)
    _hinge(surf, hip, max(pf(2.8), 4.4 * kk), Q, ow, detail)


def _hinge(surf, c, r, Q, ow, detail):
    if ow:
        pygame.draw.circle(surf, Q["out"], c, r + ow)
    pygame.draw.circle(surf, Q["tin_lo"], c, r)
    pygame.draw.circle(surf, Q["tin"], (c[0] - r * 0.12, c[1] - r * 0.12), r * 0.78)
    if r >= pf(3.2):
        _rivet(surf, c, r * 0.32, Q)


# ------------------------------------------------------------ 胳膊 ----
_PR_F = [(0.6, -FH / 2 + 8.5), (4.6, -FH / 2 + 7.2), (5.4, -FH / 2 + 3.0), (3.6, -FH / 2 - 1.2), (2.0, -FH / 2 - 0.6), (3.0, -FH / 2 + 3.2), (1.6, -FH / 2 + 5.0), (0.6, -FH / 2 + 5.0)]
_PR_B = [(-x, y) for x, y in _PR_F]


def _arm(surf, fa, ff, Q, ow, kk, detail, cel):
    sh, el = fa(0, AH / 2), fa(0, -AH / 2)
    e2 = ff(0, FH / 2)
    em = b2._lerp(el, e2, 0.5)
    wr = ff(0, -FH / 2 + 8.5)
    _hose(surf, sh, em, 3.7 * kk, Q, ow, kk, detail)
    # 夹爪（两瓣，前臂局部坐标，末端与物理前臂末端大致齐平）
    for pr in (_PR_B, _PR_F):
        _outlined(surf, [ff(x, y) for x, y in pr], Q["tin_lo"], Q["out"], ow)
        if kk * 2 >= pf(1.5):
            pygame.draw.polygon(surf, Q["tin"], [ff(x * 0.8, y + 0.4) for x, y in pr[:4]])
    # 前臂护筒
    _tube(surf, ff(0, FH / 2 - 6), wr, 3.6 * kk, 4.3 * kk, Q, ow, kk, detail, cel)
    if kk * 4 >= pf(2):
        b2._caps(surf, Q["acc"], ff(0, -FH / 2 + 12.5), ff(0, -FH / 2 + 9.5), 4.2 * kk, 4.3 * kk, cap_a=False, cap_b=False)
    _hinge(surf, em, max(pf(2.4), 3.2 * kk), Q, ow, detail)
    _hinge(surf, sh, max(pf(2.6), 4.0 * kk), Q, ow, detail)


# ------------------------------------------------------------ 躯干 ----
_CAN = _rr(-TW / 2 - 1.0, -TH / 2 + 7, TW / 2 + 1.0, TH / 2 - 0.5, 4.5)
_HIPS = _rr(-TW / 2 + 0.5, -TH / 2 - 1.0, TW / 2 - 0.5, -TH / 2 + 10, 3.0)
_BAND = (TH / 2 - 15.5, TH / 2 - 10.5)          # 胸口印刷花边带（局部 y 范围）
_PLATE = _rr(-5.5, -TH / 2 + 11.5, 6.5, -TH / 2 + 22, 1.2)
_GAUGE_C = (0.6, TH / 2 - 24.5)


def _key(surf, f, a, Q, ow, kk, k):
    """背后的发条钥匙：转轴沿躯干 -x，两片叶子随 k 绕轴转（屏幕上表现为叶子长度周期变化）。
    倒地背朝下（-x 方向在屏幕上明显朝下）时不画，避免插进地里。"""
    o, bx = f(0, 0), f(-1, 0)
    if bx[1] - o[1] > 0.55 * kk:
        return
    y = TH / 2 - 21
    ph = math.cos(k * 0.18)
    s0, s1 = f(-TW / 2 - 1, y), f(-TW / 2 - 5.5, y)
    pygame.draw.line(surf, Q["out"], s0, s1, max(2, int(2.6 * kk)) + 2 * ow)
    pygame.draw.line(surf, Q["brass"], s0, s1, max(1, int(2.6 * kk)))
    for sg in (1, -1):
        L = 6.5 * abs(ph) + 1.2
        c = f(-TW / 2 - 7, y + sg * L * 0.55)
        rx, ry = 2.0 * kk, max(1.0, L * 0.6 * kk)
        pts = [f(-TW / 2 - 7 + 2.2 * math.cos(t), y + sg * (0.6 + (L * 0.55) * (1 + math.sin(t)))) for t in
               [i * math.pi / 6 for i in range(12)]]
        _outlined(surf, pts, Q["brass"] if ph * sg > 0 else shade(Q["brass"], 0.78), Q["out"], ow)
    hub = f(-TW / 2 - 6.6, y)
    pygame.draw.circle(surf, Q["out"], hub, 2.2 * kk + ow)
    pygame.draw.circle(surf, Q["brass"], hub, 2.2 * kk)


def _torso(surf, f, a, Q, ow, kk, detail, label, cel, k):
    _key(surf, f, a, Q, ow, kk, k)
    # 脖子：短波纹管
    _hose(surf, f(0, TH / 2 - 3), f(0, TH / 2 + 4), 4.2 * kk, Q, ow, kk, False)
    # 胯部（银）
    _outlined(surf, [f(x, y) for x, y in _HIPS], Q["tin_lo"], Q["out"], ow)
    # 铁皮罐身
    _outlined(surf, [f(x, y) for x, y in _CAN], Q["shell"], Q["out"], ow)
    lit = 1 if (-0.6 * math.cos(a) + 0.8 * math.sin(a)) > 0 else -1
    if cel:
        band = b2._clip_half(_CAN, -lit, TW / 2 - 3.2)
        if len(band) >= 3:
            pygame.draw.polygon(surf, Q["shell_lo"], [f(x, y) for x, y in band])
        if detail:
            xh = (TW / 2 - 2.6) * lit
            pygame.draw.polygon(surf, Q["shell_hi"], [f(xh, -TH / 2 + 12), f(xh + 1.6 * lit, -TH / 2 + 12),
                                                    f(xh + 1.6 * lit, TH / 2 - 18), f(xh, TH / 2 - 18)])
    # 上下卷边（银）
    lw = max(1, int(kk * 2.0))
    pygame.draw.line(surf, Q["tin"], f(-TW / 2 - 0.4, TH / 2 - 2.0), f(TW / 2 + 0.4, TH / 2 - 2.0), lw)
    pygame.draw.line(surf, Q["tin"], f(-TW / 2 - 0.4, -TH / 2 + 8.6), f(TW / 2 + 0.4, -TH / 2 + 8.6), lw)
    # 印刷花边带
    if kk * 5 >= pf(2):
        y0, y1 = _BAND
        pygame.draw.polygon(surf, Q["acc"], [f(-TW / 2 - 1, y0), f(TW / 2 + 1, y0), f(TW / 2 + 1, y1), f(-TW / 2 - 1, y1)])
        if detail:
            dc = Q["shell"] if viz_fx.color_dist(Q["acc"], Q["shell"]) > 90 else Q["tin_dk"]
            for j in range(5):
                pygame.draw.circle(surf, dc, f(-TW / 2 + 1.5 + j * 4.3, (y0 + y1) / 2), max(1, 1.1 * kk))
    if not detail:
        if kk * 4 >= pf(2):
            pygame.draw.circle(surf, Q["gauge"], f(*_GAUGE_C), max(1.5, 3.4 * kk))
        return
    # 压力表（指针随 k 小幅摆动）
    gc = f(*_GAUGE_C)
    gr = 4.8 * kk
    pygame.draw.circle(surf, Q["out"], gc, gr + ow)
    pygame.draw.circle(surf, Q["tin"], gc, gr)
    pygame.draw.circle(surf, Q["gauge"], gc, gr * 0.78)
    for j in range(5):
        t = math.radians(-50 + j * 25) + a
        pygame.draw.line(surf, Q["tin_dk"], (gc[0] + math.sin(t) * gr * 0.55, gc[1] - math.cos(t) * gr * 0.55),
                         (gc[0] + math.sin(t) * gr * 0.74, gc[1] - math.cos(t) * gr * 0.74), 1)
    t = math.radians(-30 + 25 * math.sin(k * 0.21)) - a
    pygame.draw.line(surf, (210, 50, 50), gc, (gc[0] + math.sin(t) * gr * 0.66, gc[1] - math.cos(t) * gr * 0.66), max(1, int(kk)))
    pygame.draw.circle(surf, Q["tin_dk"], gc, max(1, kk * 0.9))
    # 两颗按钮
    for j, col in enumerate(((230, 64, 60), (250, 210, 70))):
        p = f(-4.0 + j * 4.6 + 2.0, TH / 2 - 31.5)
        pygame.draw.circle(surf, Q["out"], p, 1.7 * kk + 1)
        pygame.draw.circle(surf, col, p, 1.7 * kk)
        pygame.draw.circle(surf, mix(col, (255, 255, 255), 0.6), (p[0] - kk * 0.5, p[1] - kk * 0.5), max(1, 0.6 * kk))
    # 冲压号码牌
    vr._poly(surf, Q["tin_hi"], [f(x, y) for x, y in _PLATE])
    pygame.draw.lines(surf, Q["tin_dk"], True, [f(x, y) for x, y in _PLATE], 1)
    if label:
        size = int(9.5 * kk)
        while size > px(7) and text(label, size, Q["shell_dk"], True).get_width() > 10 * kk:
            size -= 1
        img = vr._bib_text(label, size, Q["shell_dk"], int(round(math.degrees(a) / 4.0)) * 4 % 360)
        cx, cy = f(0.5, -TH / 2 + 16.8)
        surf.blit(img, (cx - img.get_width() / 2, cy - img.get_height() / 2))
    # 铆钉：罐身前后两列
    for y in (-TH / 2 + 11, -TH / 2 + 25, TH / 2 - 6):
        for x in (-TW / 2 + 1.0, TW / 2 - 1.0):
            _rivet(surf, f(x, y), max(1.0, 0.85 * kk), Q)


# ------------------------------------------------------------ 头：显像管电视 + 像素点阵脸 ----
# 9x7 点阵（行从上到下）；E = 眼（会随视线平移），# = 其他
FACES = {
    "normal": [".........",
               ".EE...EE.",
               ".EE...EE.",
               ".........",
               "..#...#..",
               "...###...",
               "........."],
    "surprised": ["EEE...EEE",
                  "E.E...E.E",
                  "EEE...EEE",
                  ".........",
                  "...###...",
                  "...#.#...",
                  "...###..."],
    "grin": [".#.....#.",
             "#.#...#.#",
             ".........",
             "#########",
             "#.......#",
             ".#.....#.",
             "..#####.."],
    "dizzy": ["#.#...#.#",
              ".#.....#.",
              "#.#...#.#",
              ".........",
              ".#...#...",
              "#.#.#.#.#",
              "...#...#."],
}
NC, NR = 9, 7
# 头部局部坐标（以 R 为单位，+x 朝前，+y 朝上）
_SHELL = (-1.18, -0.98, 1.22, 1.0)
_SCR = (-0.62, -0.74, 1.04, 0.76)                 # 屏幕（带银色边框）
_GRID = (-0.5, -0.62, 0.92, 0.48)                 # 点阵区域（x0, y0, x1, y1）→ 单元 0.158R


ANT = 1.0               # 天线长度倍数（叠放时调短，避免被赛道上沿截掉）


def _head(surf, c, R, a, Q, ow, expr, k, look, detail):
    ca, sa = math.cos(a), math.sin(a)

    def L(lx, ly):
        return (c[0] + (lx * ca - ly * sa) * R, c[1] - (lx * sa + ly * ca) * R)

    def rr(x0, y0, x1, y1, r):
        return [L(x, y) for x, y in vr._rrect(x0, y0, x1, y1, r, r, 2)]

    # 兔耳天线
    if R >= pf(4.5):
        for (bx, tx, ty) in ((-0.55, -1.15, 1.85), (-0.15, 0.25, 1.95)):
            p0, p1 = L(bx, 0.95), L(bx + (tx - bx) * ANT, 0.95 + (ty - 0.95) * ANT)
            pygame.draw.line(surf, Q["out"], p0, p1, max(1, int(R * 0.09)) + 2 * ow)
            pygame.draw.line(surf, Q["tin"], p0, p1, max(1, int(R * 0.09)))
            pygame.draw.circle(surf, Q["out"], p1, R * 0.17 + ow)
            pygame.draw.circle(surf, Q["acc"], p1, R * 0.17)
        base = L(-0.35, 1.0)
        pygame.draw.circle(surf, Q["out"], base, R * 0.22 + ow)
        pygame.draw.circle(surf, Q["tin_lo"], base, R * 0.22)
    # 机壳
    _outlined(surf, rr(*_SHELL, 0.32), Q["shell"], Q["out"], ow)
    if R >= pf(6):
        pygame.draw.polygon(surf, Q["shell_lo"], rr(_SHELL[0], _SHELL[1], _SHELL[2], -0.55, 0.3))
        pygame.draw.polygon(surf, Q["shell"], rr(_SHELL[0] + 0.08, -0.62, _SHELL[2] - 0.08, _SHELL[3] - 0.08, 0.26))
    # 屏幕边框 + 玻璃
    x0, y0, x1, y1 = _SCR
    pygame.draw.polygon(surf, Q["tin"], rr(x0 - 0.1, y0 - 0.1, x1 + 0.1, y1 + 0.1, 0.34))
    pygame.draw.polygon(surf, Q["crt"], rr(x0, y0, x1, y1, 0.28))
    # 旋钮（机壳后部）
    if detail and R >= pf(7):
        for ky in (0.38, -0.2):
            p = L(-0.92, ky)
            pygame.draw.circle(surf, Q["out"], p, R * 0.17 + 1)
            pygame.draw.circle(surf, Q["tin"], p, R * 0.17)
            t = k * 0.0 + ky * 3 + a
            pygame.draw.line(surf, Q["tin_dk"], p, (p[0] + math.cos(t) * R * 0.15, p[1] - math.sin(t) * R * 0.15), 1)
        for j in range(3):                                # 底部扬声器格栅
            pygame.draw.line(surf, Q["shell_dk"], L(-1.0 + j * 0.16, -0.5), L(-1.0 + j * 0.16, -0.82), 1)
    _pixel_face(surf, L, R, Q, expr, k, look)
    # 玻璃反光
    if R >= pf(6):
        pygame.draw.lines(surf, Q["crt_hi"], False, [L(x0 + 0.12, y1 - 0.42), L(x0 + 0.14, y1 - 0.18), L(x0 + 0.4, y1 - 0.12)], max(1, int(R * 0.08)))


def _pixel_face(surf, L, R, Q, expr, k, look):
    g = Q["amber"]
    gx0, gy0, gx1, gy1 = _GRID
    cw = (gx1 - gx0) / NC
    ch = (gy1 - gy0) / NR
    cell_px = cw * R
    if cell_px < pf(1.3):                 # 太小：两个点 + 一道嘴
        pygame.draw.circle(surf, g, L(0.0, 0.08), max(1, R * 0.12))
        pygame.draw.circle(surf, g, L(0.55, 0.08), max(1, R * 0.12))
        return
    rows = FACES.get(expr, FACES["normal"])
    lx, ly = look
    ex = 0
    ey = int(round(max(-1, min(1, ly * 1.2)))) if expr == "normal" else 0
    gap = 0.14 if cell_px >= pf(3) else 0.0
    dim = mix(Q["crt"], g, 0.16)
    for r_, row in enumerate(rows):
        shift = 0
        if expr == "dizzy":                # 信号撕裂：某几行左右错位，随 k 变化（确定性）
            shift = (1 if math.sin(k * 0.5) > 0 else -1) if r_ == int(k * 0.2) % (NR + 4) else 0
        for cc in range(NC):
            ch_ = row[cc]
            if ch_ == ".":
                if gap and cell_px >= pf(3.5):       # 熄灭的像素：极暗的格子，显出点阵质感
                    cx = gx0 + (cc + 0.5) * cw
                    cy = gy1 - (r_ + 0.5) * ch
                    pygame.draw.circle(surf, dim, L(cx, cy), max(1, cell_px * 0.18))
                continue
            dc, dr = (ex, ey) if ch_ == "E" else (0, 0)
            col = cc + dc + shift
            rr_ = r_ + dr
            xa = gx0 + col * cw + cw * gap
            xb = gx0 + (col + 1) * cw - cw * gap
            yb = gy1 - rr_ * ch - ch * gap
            ya = gy1 - (rr_ + 1) * ch + ch * gap
            if xa < _SCR[0] or xb > _SCR[2]:
                continue
            pygame.draw.polygon(surf, g, [L(xa, ya), L(xb, ya), L(xb, yb), L(xa, yb)])
    if expr == "dizzy" and R >= pf(5):    # 头顶转圈的小齿轮/星
        for j in range(3):
            t = k * 0.12 + j * 2.094
            sx = L(0, 0)[0] + math.cos(t) * R * 1.6
            sy = L(0, 0)[1] - R * 2.2 + math.sin(t) * R * 0.4
            rs = max(pf(2), R * 0.26)
            star = [(sx + math.cos(i * math.pi / 4) * (rs if i % 2 == 0 else rs * 0.4),
                     sy + math.sin(i * math.pi / 4) * (rs if i % 2 == 0 else rs * 0.4)) for i in range(8)]
            pygame.draw.polygon(surf, AMBER, star)


# ------------------------------------------------------------ 入口 --------
def draw_runner(surf, v, pose, color, expr, k, lane_i=0, label="", look=None):
    kk = v.scale / PPM
    ow = vr.outline_width(v.scale) if viz_fx.S == 1.0 else px(vr.outline_width(v.scale / viz_fx.S))
    detail = v.scale >= pf(80)
    cel = v.scale >= 70
    head = None
    a0 = pose[0][2]
    fx_, fy_ = math.cos(a0), math.sin(a0)
    if look is not None:
        fx_, fy_ = fx_ * 0.8 + look[0] * 0.5, fy_ * 0.8 + look[1] * 0.5
    ln = math.hypot(fx_, fy_) or 1.0
    # 点阵眼睛的偏移在头部局部坐标里取：把屏幕视线转回头部局部
    sx, sy = fx_ / ln, fy_ / ln
    look_l = (sx * math.cos(a0) + sy * math.sin(a0) - 1.0, -sx * math.sin(a0) + sy * math.cos(a0))
    for b, far in vr.LAYERS:
        Q = palette(color, far, lane_i)
        if b == 0:
            f = vr._xf(v, pose[0])
            _torso(surf, f, a0, Q, ow, kk, detail, label, cel, k)
            c = f(0, TH / 2 + HR)
            R = HR * kk
            _head(surf, c, R, a0, Q, ow, expr, k, (look_l[0] * 2.0, -look_l[1] * 2.0), detail)
            head = (c, R)
        elif b in (2, 4):
            foot = pose[vr.FOOT_IDX[0] if b == 2 else vr.FOOT_IDX[1]]
            _leg(surf, vr._xf(v, pose[b - 1]), vr._xf(v, pose[b]), vr._xf(v, vr.shoe_pose(foot)), Q, ow, kk, detail, cel)
        elif b in (6, 8):
            _arm(surf, vr._xf(v, pose[b - 1]), vr._xf(v, pose[b]), Q, ow, kk, detail, cel)
    return head
