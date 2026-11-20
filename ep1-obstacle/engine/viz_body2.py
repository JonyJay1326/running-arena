"""--body-style v2：运动服 Q 版小人（2026-10-29，纯渲染，不碰物理/计分/随机数）。

接口与 viz_runner.draw_runner 完全相同（visualizer.Renderer._draw_runner 按 BODY_STYLE 二选一）：
    head = draw_runner(surf, v, pose, color, expr, k, lane_i=i, label="R", look=(vx, vy))
颜色全部来自 viz_runner.palette（角色化时 visualizer 已把主题色 主色 + 点缀色 写进它的缓存，同屏相近色拉开逻辑不变）。

和 v1 的区别（物理刚体尺寸不变，画法都收在刚体轮廓里）：
  - 整条腿 / 整条胳膊先画一遍描边、再画填充：膝、肘、肩、髋处没有内部描边，关节圆润衔接（v1 是一节节胶囊叠起来）
  - 四肢是两端不同粗细的锥形胶囊（大腿根粗、膝细，小腿肚 → 脚踝收细；上臂 → 手腕收细），宽度不超过物理盒宽
  - 两段式明暗（cel shading）：先画暗部、再把亮部往光源方向（屏幕左上）偏一点画上去，背光侧留一条暗边；外加高光细线
  - 躯干 = 短袖运动 T 恤：圆肩、收腰、点缀色圆领 + 袖口 + 侧边竖条纹，号码布（选手缩写）
  - 短裤：主色深色调、点缀色侧条和裤脚滚边；袜子：白色 + 两道点缀色条；跑鞋：鞋面 + 白色厚鞋底 + 鞋头包边 + 侧面点缀色弧线 + 鞋带
  - 手 = 圆手（略大于前臂宽），手腕点缀色护腕
  - 远侧肢体整体压暗偏冷（空气透视，沿用 viz_runner 的远侧调色板）
  - 头部（不贴头像时）沿用 v1 的头/脸/头带
缩放较小时（叠放 4 人 720p）自动省略高光、鞋带、号码字等细节。
"""
import math

import pygame
import pygame.gfxdraw as gfx

import physics
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
LIGHT = (-0.6, -0.8)            # 光源方向（屏幕坐标，左上）

# 造型参数（物理 px，局部坐标）—— 调这里即可
STYLE = dict(
    sleeve="tee",               # tee = 短袖；tank = 背心（露胳膊）
    shading="cel",              # cel = 两段明暗；flat = 只有高光线
    outline="ink",              # ink = 统一深色描边；tinted = 描边用各部件颜色的深色调
)
# 各段半径（物理 px）：起点 → 终点
R_THIGH = (THW / 2, THW / 2 * 0.80)          # 大腿根 6 → 膝 4.8
R_SHIN = (SHW / 2 * 0.98, SHW / 2 * 0.66)    # 小腿肚 4.9 → 踝 3.3
R_ARM = (AW / 2, AW / 2 * 0.80)              # 上臂 5 → 肘 4
R_FORE = (FW / 2 * 0.98, FW / 2 * 0.74)      # 前臂 3.9 → 腕 3.0
HAND_R = FW / 2 * 1.3                        # 圆手（前臂盒宽 8 → 直径 10.4，略大一圈更 Q）


# ------------------------------------------------------------ 几何原语（屏幕坐标）
def _taper(a, b, ra, rb):
    """a→b 的锥形胶囊外轮廓（两端圆 + 外公切线）。返回 (多边形或 None, 两个端圆)。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    d = math.hypot(dx, dy)
    if d < 1e-6 or abs(ra - rb) >= d:
        return None
    ux, uy = dx / d, dy / d
    nx, ny = -uy, ux
    s = (ra - rb) / d
    c = math.sqrt(max(0.0, 1 - s * s))
    w1 = (ux * s + nx * c, uy * s + ny * c)
    w2 = (ux * s - nx * c, uy * s - ny * c)
    return [(a[0] + ra * w1[0], a[1] + ra * w1[1]), (b[0] + rb * w1[0], b[1] + rb * w1[1]),
            (b[0] + rb * w2[0], b[1] + rb * w2[1]), (a[0] + ra * w2[0], a[1] + ra * w2[1])]


def _poly(surf, col, pts, aa=False):
    if aa:
        gfx.aapolygon(surf, pts, col)
        gfx.filled_polygon(surf, pts, col)
    else:
        pygame.draw.polygon(surf, col, pts)


def _circle(surf, col, c, r, aa=False):
    if aa and r >= 2:
        x, y, ri = int(round(c[0])), int(round(c[1])), int(round(r))
        gfx.aacircle(surf, x, y, ri, col)
        gfx.filled_circle(surf, x, y, ri, col)
    else:
        pygame.draw.circle(surf, col, c, r)


def _caps(surf, col, a, b, ra, rb, grow=0.0, aa=False, cap_a=True, cap_b=True):
    """画一条锥形胶囊（整体外扩 grow px）。cap_*=False 时该端是平口。"""
    ra, rb = ra + grow, rb + grow
    q = _taper(a, b, ra, rb)
    if q is None:
        _circle(surf, col, a if ra >= rb else b, max(ra, rb), aa)
        return
    if not cap_a or not cap_b:
        dx, dy = b[0] - a[0], b[1] - a[1]
        d = math.hypot(dx, dy)
        ux, uy = dx / d, dy / d
        nx, ny = -uy, ux
        ea = 0 if cap_a else grow
        eb = 0 if cap_b else grow
        if not cap_a:
            q[0] = (a[0] + nx * ra - ux * ea, a[1] + ny * ra - uy * ea)
            q[3] = (a[0] - nx * ra - ux * ea, a[1] - ny * ra - uy * ea)
        if not cap_b:
            q[1] = (b[0] + nx * rb + ux * eb, b[1] + ny * rb + uy * eb)
            q[2] = (b[0] - nx * rb + ux * eb, b[1] - ny * rb + uy * eb)
    _poly(surf, col, q, aa)
    if cap_a:
        _circle(surf, col, a, ra, aa)
    if cap_b:
        _circle(surf, col, b, rb, aa)


def _lit_normal(a, b):
    """肢体轴的单位法线中朝向光源的那一个。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    d = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / d, dx / d
    if nx * LIGHT[0] + ny * LIGHT[1] < 0:
        nx, ny = -nx, -ny
    return nx, ny


def _shaded(surf, a, b, ra, rb, lo, base, cel, cap_a=True, cap_b=True):
    """填充一段肢体：cel 时先整段画暗色，再把亮色缩小一点、往光源方向偏移画上去（背光侧留暗边）。"""
    if not cel:
        _caps(surf, base, a, b, ra, rb, cap_a=cap_a, cap_b=cap_b)
        return
    _caps(surf, lo, a, b, ra, rb, cap_a=cap_a, cap_b=cap_b)
    nx, ny = _lit_normal(a, b)
    m = 0.24
    oa, ob = ra * m * 0.9, rb * m * 0.9
    _caps(surf, base, (a[0] + nx * oa, a[1] + ny * oa), (b[0] + nx * ob, b[1] + ny * ob),
          ra * (1 - m), rb * (1 - m), cap_a=cap_a, cap_b=cap_b)


def _hl(surf, a, b, ra, rb, col, lo=0.2, hi=0.75, wmin=1):
    """亮侧高光细线。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    ln = math.hypot(dx, dy)
    if ln < pf(4) or min(ra, rb) < pf(2.6):
        return
    nx, ny = _lit_normal(a, b)
    p0 = (a[0] + dx * lo + nx * (ra + (rb - ra) * lo) * 0.5, a[1] + dy * lo + ny * (ra + (rb - ra) * lo) * 0.5)
    p1 = (a[0] + dx * hi + nx * (ra + (rb - ra) * hi) * 0.5, a[1] + dy * hi + ny * (ra + (rb - ra) * hi) * 0.5)
    pygame.draw.line(surf, col, p0, p1, max(wmin, int(min(ra, rb) * 0.34)))


def _lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


# ------------------------------------------------------------ 调色板 ----
_pal2 = {}


def palette(color, far, lane_i):
    """在 viz_runner.palette（主题色已在里面）基础上补 v2 需要的明暗色。"""
    P = vr.palette(color, far, lane_i)
    key = id(P)
    q = _pal2.get(key)
    if q is None or q[0] is not P:
        Q = dict(P)
        acc = P["band"]
        Q["acc"] = acc
        Q["skin_lo"] = mix(shade(P["skin"], 0.86), (200, 120, 110), 0.18)
        Q["jersey_lo2"] = mix(shade(P["jersey"], 0.78), (40, 40, 80), 0.10)
        Q["shorts_lo"] = shade(P["shorts"], 0.72)
        Q["sock_lo"] = shade(P["sock"], 0.86)
        Q["shoe_lo"] = shade(P["shoe"], 0.78)
        Q["shoe_hi"] = mix(P["shoe"], (255, 255, 255), 0.35)
        # 鞋上的点缀色：点缀色与鞋面太近（白点缀时鞋本来就用主色）就改用白色
        Q["shoe_acc"] = (250, 250, 252) if viz_fx.color_dist(acc, P["shoe"]) < 90 else acc
        # 袖口 / 领口 / 侧条：点缀色与球衣太近（深色主色 + 白点缀等不会，但默认赛道色时点缀=主色）就用白色
        Q["trim"] = (250, 250, 252) if viz_fx.color_dist(acc, P["jersey"]) < 90 else acc
        Q["short_trim"] = (250, 250, 252) if viz_fx.color_dist(Q["trim"], P["shorts"]) < 90 else Q["trim"]
        Q["glove"] = P["skin"]
        Q["glove_lo"] = Q["skin_lo"]
        q = _pal2[key] = (P, Q)
    return q[1]


def _oc(Q, fill):
    """描边色：ink = 统一深色；tinted = 部件颜色的深色调。"""
    if STYLE["outline"] == "tinted":
        return mix(shade(fill, 0.45), Q["out"], 0.5)
    return Q["out"]


# ------------------------------------------------------------ 身体各部分
def _leg(surf, ft, fs, fsh, Q, ow, kk, detail, cel):
    hip, knee = ft(0, THH / 2), ft(0, -THH / 2)
    k2, ank = fs(0, SHH / 2), fs(0, -SHH / 2)
    knee_m = _lerp(knee, k2, 0.5)                   # 两刚体的膝点平均，弯膝时不出缝
    rt0, rt1 = R_THIGH[0] * kk, R_THIGH[1] * kk
    rs0, rs1 = R_SHIN[0] * kk, R_SHIN[1] * kk
    aa = ow >= 2
    out = Q["out"]
    # 1) 整条腿的描边（大腿 + 小腿 + 鞋）一次画完 → 膝盖处没有内部描边
    if ow:
        _caps(surf, out, hip, knee_m, rt0, rt1, ow, aa)
        _caps(surf, out, knee_m, ank, rs0, rs1, ow, aa)
        _poly(surf, out, [fsh(x, y) for x, y in vr._off("shoe", vr.SHOE, ow / kk)], aa)
    # 2) 填充：皮肤
    _shaded(surf, hip, knee_m, rt0, rt1, Q["skin_lo"], Q["skin"], cel)
    _shaded(surf, knee_m, ank, rs0, rs1, Q["skin_lo"], Q["skin"], cel)
    if detail:
        _hl(surf, knee_m, ank, rs0, rs1, Q["skin_hi"], 0.15, 0.5)
    # 3) 短裤：大腿上 60%，略宽，平裤脚 + 点缀色滚边 + 侧条
    hem_t = 0.60
    hem = _lerp(hip, knee_m, hem_t)
    rh = rt0 + (rt1 - rt0) * hem_t
    g = 0.9 * kk
    if ow:
        _caps(surf, _oc(Q, Q["shorts"]), hip, hem, rt0 + g, rh + g, ow, aa, cap_b=False)
    _shaded(surf, hip, hem, rt0 + g, rh + g, Q["shorts_lo"], Q["shorts"], cel, cap_b=False)
    if rh >= pf(3):
        a2 = _lerp(hip, knee_m, hem_t - 0.07)
        _caps(surf, Q["short_trim"], a2, hem, rh + g, rh + g, cap_a=False, cap_b=False)
    if detail:
        _hl(surf, hip, hem, rt0 + g, rh + g, mix(Q["shorts"], (255, 255, 255), 0.22), 0.2, 0.8)
    # 4) 袜子：踝上 15px，白色 + 两道点缀色条
    st = fs(0, -SHH / 2 + 15)
    sw0 = rs0 + (rs1 - rs0) * ((SHH - 15) / SHH) + 0.35 * kk
    sw1 = rs1 + 0.35 * kk
    if ow:
        _caps(surf, _oc(Q, Q["sock"]), st, ank, sw0, sw1, ow, aa, cap_a=False, cap_b=False)
    _shaded(surf, st, ank, sw0, sw1, Q["sock_lo"], Q["sock"], cel, cap_a=False, cap_b=False)
    if sw1 >= pf(2.2):
        for t0, t1 in ((0.10, 0.24), (0.34, 0.46)):
            a_, b_ = _lerp(st, ank, t0), _lerp(st, ank, t1)
            r_ = sw0 + (sw1 - sw0) * (t0 + t1) / 2
            _caps(surf, Q["stripe"], a_, b_, r_, r_, cap_a=False, cap_b=False)
    # 5) 跑鞋（跟着脚掌刚体）
    _shoe(surf, fsh, Q, ow, kk, detail, cel)


# 跑鞋局部几何（小腿局部坐标，与 viz_runner.SHOE 同一套基准：底边 = 物理脚掌盒底边）
_FB, _FX0, _FX1 = vr._FOOT_BOT, vr._FOOT_X0, vr._FOOT_X1
_SOLE_T = vr._SOLE_TOP + 0.5
SOLE2 = [(x, min(y, _SOLE_T)) for x, y in vr.SHOE]
TOE_CAP = [(x, y) for x, y in vr.SHOE if x > _FX1 - 6.5 and y <= _FB + 5.2]
# 侧面点缀色弧线（从鞋跟上方斜到鞋头上方）
SWOOSH = [(_FX0 + 1.5, _FB + 5.6), (_FX0 + 8, _FB + 4.0), (_FX1 - 9, _FB + 4.4), (_FX1 - 3.5, _FB + 6.6),
          (_FX1 - 9.5, _FB + 6.2), (_FX0 + 8.5, _FB + 6.0)]


def _shoe(surf, f, Q, ow, kk, detail, cel):
    vr._poly(surf, Q["shoe"], [f(x, y) for x, y in vr.SHOE])
    if cel:
        # 鞋面下半部暗一点（立体感）
        lo = [(x, min(y, _FB + 5.0)) for x, y in vr.SHOE]
        _poly(surf, Q["shoe_lo"], [f(x, y) for x, y in lo])
    if kk * 3 >= pf(1.6):
        _poly(surf, Q["shoe_acc"], [f(x, y) for x, y in SWOOSH])
    # 白色厚鞋底 + 鞋底线
    _poly(surf, Q["sole"], [f(x, y) for x, y in SOLE2])
    pygame.draw.line(surf, Q["out"], f(_FX0 - 0.6, _SOLE_T), f(_FX1 + 0.9, _SOLE_T), max(1, ow - 1) if ow else px(1))
    if detail:
        # 鞋带（踝前三道）+ 鞋头高光
        for j in range(3):
            x = SHW / 2 + 2.2 + j * 2.8
            y = _FB + 10.0 - j * 1.15
            pygame.draw.line(surf, Q["lace"], f(x - 1.3, y - 1.4), f(x + 1.3, y + 1.0), max(1, int(kk * 1.4)))
        pygame.draw.line(surf, Q["shoe_hi"], f(_FX1 - 6.5, _FB + 5.4), f(_FX1 - 1.5, _FB + 4.4), max(1, int(kk * 1.2)))
        # 鞋底纹（几道短竖线）
        for j in range(5):
            x = _FX0 + 3 + j * (_FX1 - _FX0 - 6) / 4
            pygame.draw.line(surf, shade(Q["sole"], 0.8), f(x, _FB + 0.6), f(x, _SOLE_T - 0.7), 1)


def _arm(surf, fa, ff, Q, ow, kk, detail, cel):
    sh, el = fa(0, AH / 2), fa(0, -AH / 2)
    e2, wr = ff(0, FH / 2), ff(0, -FH / 2 + HAND_R)
    el_m = _lerp(el, e2, 0.5)
    hand = ff(0, -FH / 2 + HAND_R * 0.95)
    ra0, ra1 = R_ARM[0] * kk, R_ARM[1] * kk
    rf0, rf1 = R_FORE[0] * kk, R_FORE[1] * kk
    hr = HAND_R * kk
    aa = ow >= 2
    out = Q["out"]
    if ow:
        _caps(surf, out, sh, el_m, ra0, ra1, ow, aa)
        _caps(surf, out, el_m, wr, rf0, rf1, ow, aa)
        _circle(surf, out, hand, hr + ow, aa)
    _shaded(surf, sh, el_m, ra0, ra1, Q["skin_lo"], Q["skin"], cel)
    _shaded(surf, el_m, wr, rf0, rf1, Q["skin_lo"], Q["skin"], cel)
    if detail:
        _hl(surf, el_m, wr, rf0, rf1, Q["skin_hi"], 0.15, 0.55)
    # 护腕（点缀色）
    if rf1 >= pf(1.8):
        a_, b_ = ff(0, -FH / 2 + 15), ff(0, -FH / 2 + 10.5)
        r_ = rf1 + 0.7 * kk
        if ow:
            _caps(surf, out, a_, b_, r_, r_, max(1, ow - 1), cap_a=False, cap_b=False)
        _caps(surf, Q["acc"] if viz_fx.color_dist(Q["acc"], Q["skin"]) > 60 else Q["jersey"], a_, b_, r_, r_, cap_a=False, cap_b=False)
    # 圆手
    if cel:
        _circle(surf, Q["glove_lo"], hand, hr)
        nx, ny = LIGHT
        _circle(surf, Q["glove"], (hand[0] + nx * hr * 0.18, hand[1] + ny * hr * 0.18), hr * 0.8)
    else:
        _circle(surf, Q["glove"], hand, hr)
    # 短袖（T 恤）：上臂上 48%，略宽，袖口点缀色
    if STYLE["sleeve"] == "tee":
        t = 0.48
        sl = _lerp(sh, el_m, t)
        g = 0.9 * kk
        r0, r1 = ra0 + g, ra0 + (ra1 - ra0) * t + g
        if ow:
            _caps(surf, _oc(Q, Q["jersey"]), sh, sl, r0, r1, ow, aa, cap_b=False)
        _shaded(surf, sh, sl, r0, r1, Q["jersey_lo2"], Q["jersey"], cel, cap_b=False)
        if r1 >= pf(3):
            _caps(surf, Q["trim"], _lerp(sh, el_m, t - 0.09), sl, r1, r1, cap_a=False, cap_b=False)
        if detail:
            _hl(surf, sh, sl, r0, r1, Q["jersey_hi2"], 0.15, 0.7)


# 躯干：T 恤轮廓（圆肩、轻微收腰、下摆略宽），全部在 20x60 盒内
def _tee_shape():
    w2, h2 = TW / 2, TH / 2
    pts = [(-w2 + 1.4, -h2), (w2 - 1.4, -h2),                 # 下摆
           (w2 - 0.6, -h2 + 6), (w2 - 1.6, -h2 + 22), (w2 - 1.0, h2 - 16),      # 前侧：收腰再放
           (w2, h2 - 9), (w2 - 2.2, h2 - 2.6), (w2 - 6, h2),                     # 前肩
           (-w2 + 6, h2), (-w2 + 2.2, h2 - 2.6), (-w2, h2 - 9),                  # 后肩
           (-w2 + 1.0, h2 - 16), (-w2 + 1.6, -h2 + 22), (-w2 + 0.6, -h2 + 6)]
    return vr._chaikin(pts, 2)


TEE = _tee_shape()
_TEE_REV = list(reversed(TEE))          # _offset 需要逆时针
TANK = vr._chaikin([(-TW / 2 + 1.4, -TH / 2), (TW / 2 - 1.4, -TH / 2), (TW / 2 - 0.8, -TH / 2 + 20),
                    (TW / 2 - 1.0, TH / 2 - 10), (TW / 2 - 3.2, TH / 2 - 3), (TW / 2 - 5.5, TH / 2),
                    (-TW / 2 + 5.5, TH / 2), (-TW / 2 + 3.2, TH / 2 - 3), (-TW / 2 + 1.0, TH / 2 - 10),
                    (-TW / 2 + 0.8, -TH / 2 + 20)], 2)
_SHORTS_TOP = -TH / 2 + 13
WAIST = vr._rrect(-TW / 2 - 0.6, -TH / 2 - 0.6, TW / 2 + 0.6, _SHORTS_TOP, 0, 4.6)
BIB2 = vr._rrect(-5.0, -TH / 2 + 16, 8.0, -TH / 2 + 34, 2, 2, 2)
_BIB2_C = (1.5, -TH / 2 + 25)
_off2 = {}


def _offc(name, pts, d):
    key = (name, round(d, 2))
    r = _off2.get(key)
    if r is None:
        # 统一成逆时针再外扩
        area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]))
        src = pts if area > 0 else list(reversed(pts))
        r = _off2[key] = vr._offset(src, d)
    return r


_clip_cache = {}


def _clip_half(pts, sgn, x0):
    """多边形与半平面 sgn * x >= x0 的交（Sutherland–Hodgman），按参数缓存。"""
    key = (id(pts), sgn, x0)
    r = _clip_cache.get(key)
    if r is not None:
        return r
    out = []
    n = len(pts)
    for i in range(n):
        (xa, ya), (xb, yb) = pts[i], pts[(i + 1) % n]
        ina, inb = sgn * xa >= x0, sgn * xb >= x0
        if ina:
            out.append((xa, ya))
        if ina != inb:
            t = (x0 / sgn - xa) / (xb - xa)
            out.append((x0 / sgn, ya + (yb - ya) * t))
    _clip_cache[key] = out
    return out


def _torso(surf, f, a, Q, ow, kk, detail, label, cel):
    aa = ow >= 2
    shape = TEE if STYLE["sleeve"] == "tee" else TANK
    # 脖子
    vr._seg(surf, f(0, TH / 2 - 4), f(0, TH / 2 + 3), 3.4 * kk, Q["skin"], Q["out"], ow)
    if ow:
        _poly(surf, _oc(Q, Q["jersey"]), [f(x, y) for x, y in _offc("tee" + STYLE["sleeve"], shape, ow / kk)], aa)
    _poly(surf, Q["jersey"], [f(x, y) for x, y in shape])
    lit = 1 if (-0.6 * math.cos(a) + 0.8 * math.sin(a)) > 0 else -1     # 局部 +x 侧朝光源时为 1
    if cel:
        # 背光侧一条暗部（轮廓 ∩ 背光侧 4px 宽的半平面）
        band = _clip_half(shape, -lit, TW / 2 - 4.2)
        if len(band) >= 3:
            _poly(surf, Q["jersey_lo2"], [f(x, y) for x, y in band])
    # 侧边竖条纹（点缀色，从腋下到下摆，略偏后）
    if kk * 2.2 >= pf(1.2):
        sx = -2.6
        _poly(surf, Q["trim"], [f(sx - 1.1, -TH / 2 + 13), f(sx + 1.1, -TH / 2 + 13), f(sx + 1.1, TH / 2 - 11), f(sx - 1.1, TH / 2 - 9)])
    if detail and cel:
        x_hi = 5.6 * lit
        _poly(surf, Q["jersey_hi"], [f(x_hi, -TH / 2 + 15), f(x_hi + 1.8 * lit, -TH / 2 + 15),
                                     f(x_hi + 1.8 * lit, TH / 2 - 10), f(x_hi, TH / 2 - 8)])
    # 领口：点缀色圆领（在肩线上方一圈）
    if kk * 2 >= pf(1.0):
        pygame.draw.lines(surf, Q["trim"], False, [f(-4.2, TH / 2 - 0.8), f(0, TH / 2 - 2.6), f(4.6, TH / 2 - 0.6)], max(px(1), int(kk * 1.9)))
    # 短裤腰头（短裤颜色）+ 腰带点缀线
    if ow:
        _poly(surf, _oc(Q, Q["shorts"]), [f(x, y) for x, y in vr._off("band", WAIST, ow / kk)], aa)
    _poly(surf, Q["shorts"], [f(x, y) for x, y in WAIST])
    pygame.draw.line(surf, Q["short_trim"], f(-TW / 2, _SHORTS_TOP - 1.4), f(TW / 2, _SHORTS_TOP - 1.4), max(1, int(kk * 1.3)))
    # 号码布 + 选手缩写
    if detail and label:
        vr._shape(surf, f, BIB2, "bib2", Q["bib"], Q["out"], px(1), kk)
        size = int(12 * kk)
        img = text(label, size, Q["jersey_lo"], True)
        while size > px(7) and img.get_width() > 11 * kk:
            size -= 1
            img = text(label, size, Q["jersey_lo"], True)
        deg = int(round(math.degrees(a) / 4.0)) * 4 % 360
        img = vr._bib_text(label, size, Q["jersey_lo"], deg)
        cx, cy = f(*_BIB2_C)
        surf.blit(img, (cx - img.get_width() / 2, cy - img.get_height() / 2))


# ------------------------------------------------------------ 入口 --------
def draw_runner(surf, v, pose, color, expr, k, lane_i=0, label="", look=None):
    kk = v.scale / PPM
    ow = vr.outline_width(v.scale) if viz_fx.S == 1.0 else px(vr.outline_width(v.scale / viz_fx.S))
    detail = v.scale >= pf(80)
    cel = STYLE["shading"] == "cel" and v.scale >= 70        # 实际像素：肢体足够粗时才画两段明暗（720p 叠放时关闭）
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
            _torso(surf, f, a0, Q, ow, kk, detail, label, cel)
            c = f(0, TH / 2 + HR)
            R = HR * kk
            vr._head(surf, c, R, a0, Q, ow, expr, k, look_s)
            head = (c, R)
        elif b in (2, 4):
            foot = pose[vr.FOOT_IDX[0] if b == 2 else vr.FOOT_IDX[1]]
            _leg(surf, vr._xf(v, pose[b - 1]), vr._xf(v, pose[b]), vr._xf(v, vr.shoe_pose(foot)), Q, ow, kk, detail, cel)
        elif b in (6, 8):
            _arm(surf, vr._xf(v, pose[b - 1]), vr._xf(v, pose[b]), Q, ow, kk, detail, cel)
    return head
