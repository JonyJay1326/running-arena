"""小人外观（纯渲染，不碰物理/计分/随机数）—— visualizer 的 "选手造型" 模块。

用法（visualizer.Renderer._draw_runner 里只有一行调用）：
    head = viz_runner.draw_runner(surf, v, pose, color, expr, k, lane_i=i, label="R", look=(vx, vy))
  - v      : visualizer.View（需要 .scale/.ax/.ay，世界 m → 屏幕 px）
  - pose   : 11 个刚体的 (x_px, y_px, angle)，顺序固定为
             躯干、左大腿、左小腿、右大腿、右小腿、左上臂、左前臂、右上臂、右前臂、左脚掌、右脚掌（之后的栏架忽略）
  - color  : 赛道主色（球衣/头带/鞋/护腕都用它，保持每道的颜色身份）
  - expr   : "normal" | "dizzy" | "surprised" | "grin"
  - k      : 回放位置（可为小数帧号，只用于头带飘动/晕眩星星等小动画，保证确定性）
  - lane_i : 赛道序号（只决定发色）；label：球衣号码布上的字（选手缩写）
  - look   : 躯干速度 (vx, vy)（m/s，世界坐标），眼珠朝运动方向看；None = 朝前
  返回 ((头心 x, y), 头半径 px)，供头顶气泡定位。

造型（全部按物理尺寸画，接地处与碰撞形状对齐）：
  - 四肢 = 以关节点为圆心的胶囊（宽度 = 物理盒宽），统一描边粗细（随缩放 1/2/3px，外描边抗锯齿）
  - 球衣（赛道色，侧面高光/暗部 + 号码布写选手缩写）+ 短裤（赛道色深色调，腰带 + 大腿上 58%）
  - 皮肤色小腿/前臂，白色袜子带赛道色条纹，跑鞋 = 赛道色鞋面 + 白色鞋底线 + 鞋带；
    脚掌是独立刚体（踝关节随膝联动）：跑鞋按脚掌刚体的位姿画（shoe_pose 由脚掌位姿反推一个"虚拟小腿坐标系"，
    鞋的造型仍用小腿局部坐标描述），鞋底底边就是物理脚掌盒的底边，不会悬空也不会陷进地里
  - 手 = 前臂末端的小圆（底边与物理前臂末端齐平），赛道色护腕
  - 头：头发（按赛道换发色）、赛道色头带（带飘动的结）、耳朵、鼻子、腮红、带瞳孔的眼睛（瞳孔看运动方向）、眉毛
  - 远侧肢体整体压暗并偏冷（空气透视），近侧正常；层次顺序与原来一致
  - 缩放较小时（4 道叠放）自动省略高光/鞋带/号码字等细节，只保留轮廓与配色

移植到另一份 visualizer.py：把本文件放到 visualizer.py 同目录，然后
  1) import viz_runner
  2) Renderer._draw_runner(...) 的函数体换成调用 viz_runner.draw_runner（见本仓库 visualizer.py）
  3) _draw_lane 里调用 _draw_runner 时多传 lane_i / look（可选，不传也能画）
"""
import math

import pygame
import pygame.gfxdraw as gfx

import physics
from viz_fx import INK, mix, shade, text

PPM = physics.PPM
TW, TH = physics.TORSO_W, physics.TORSO_H
HR = physics.HEAD_R
THW, THH = physics.THIGH_W, physics.THIGH_H
SHW, SHH = physics.SHIN_W, physics.SHIN_H
AW, AH = physics.ARM_W, physics.ARM_H
FW, FH = physics.FORE_W, physics.FORE_H
FOOT_L, FOOT_H, HEEL = physics.FOOT_L, physics.FOOT_H, physics.FOOT_HEEL_SHIFT

# 画的顺序（与原 Renderer.LAYERS 相同）：远侧手臂、远侧腿、躯干+头、近侧腿、近侧手臂
LAYERS = [(5, True), (6, True), (1, True), (2, True), (0, False), (3, False), (4, False), (7, False), (8, False)]

SKIN = (250, 214, 182)
HAIR = [(84, 52, 34), (42, 38, 48), (168, 92, 44), (228, 186, 92)]     # 按赛道序号
OUT = (36, 34, 46)                 # 统一描边色
BLUSH = (246, 160, 150)
MOUTH = (150, 40, 52)
FAR_TINT = (64, 78, 104)           # 远侧肢体混入的冷色


# ------------------------------------------------------------ 局部几何（物理 px，预先算好）
def _chaikin(pts, it=2):
    for _ in range(it):
        out = []
        n = len(pts)
        for i in range(n):
            (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
            out += [(0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1), (0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1)]
        pts = out
    return pts


def _rrect(x0, y0, x1, y1, r_top, r_bot, n=3):
    """圆角矩形（逆时针），上/下两对角可不同半径。"""
    pts = []
    for cx, cy, r, a0 in ((x1 - r_bot, y0 + r_bot, r_bot, -90), (x1 - r_top, y1 - r_top, r_top, 0),
                          (x0 + r_top, y1 - r_top, r_top, 90), (x0 + r_bot, y0 + r_bot, r_bot, 180)):
        if r <= 0:
            pts.append((cx, cy))
            continue
        for j in range(n + 1):
            a = math.radians(a0 + 90 * j / n)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _offset(pts, d):
    """按顶点法线向外扩 d（用于描边外轮廓；输入为逆时针的近凸多边形）。"""
    n = len(pts)
    out = []
    for i in range(n):
        (xa, ya), (xb, yb), (xc, yc) = pts[i - 1], pts[i], pts[(i + 1) % n]
        e1 = (yb - ya, -(xb - xa))
        e2 = (yc - yb, -(xc - xb))
        l1, l2 = math.hypot(*e1) or 1, math.hypot(*e2) or 1
        nx, ny = e1[0] / l1 + e2[0] / l2, e1[1] / l1 + e2[1] / l2
        ln = math.hypot(nx, ny) or 1
        nx, ny = nx / ln, ny / ln
        cosh = max(0.5, (nx * e1[0] / l1 + ny * e1[1] / l1))       # 斜接长度，限制尖角
        out.append((xb + nx * d / cosh, yb + ny * d / cosh))
    return out


_FOOT_BOT = -SHH / 2 - FOOT_H + 1          # 物理脚掌盒底边（小腿局部 y）
_FOOT_X0, _FOOT_X1 = HEEL - FOOT_L / 2, HEEL + FOOT_L / 2
# 跑鞋：底边贴着脚掌盒底边，鞋头圆、鞋跟略高，鞋口包住脚踝
# 鞋的轮廓以踝点（小腿中线 x=0）、脚跟 _FOOT_X0、脚尖 _FOOT_X1 为基准：圆鞋头、鞋面斜到踝前、鞋口包住脚踝、鞋跟略高
SHOE = _chaikin([(_FOOT_X0 - 0.5, _FOOT_BOT), (_FOOT_X1 + 0.5, _FOOT_BOT), (_FOOT_X1 + 1.6, _FOOT_BOT + 2.4),
                 (_FOOT_X1 + 0.4, _FOOT_BOT + 4.6), (_FOOT_X1 - 4.0, _FOOT_BOT + 6.0), (SHW / 2 + 3.5, _FOOT_BOT + 8.6),
                 (SHW / 2 + 0.8, _FOOT_BOT + 12.5), (-SHW / 2 - 1.0, _FOOT_BOT + 13.0), (_FOOT_X0 + 0.2, _FOOT_BOT + 9.5),
                 (_FOOT_X0 - 1.0, _FOOT_BOT + 3.0)])
# chaikin 会把底边两个角往里收，但底边本身仍在 y = _FOOT_BOT 上
_SOLE_TOP = _FOOT_BOT + 2.3
SOLE = [(x, min(y, _SOLE_TOP)) for x, y in SHOE]


def set_foot(foot_l, heel):
    """跳远身体改了脚长/踝点位置时，鞋的轮廓跟着物理脚掌盒走（默认 = physics.FOOT_L / FOOT_HEEL_SHIFT）。"""
    global FOOT_L, HEEL, _FOOT_X0, _FOOT_X1, SHOE, SOLE, _FOOT_C
    FOOT_L, HEEL = foot_l, heel
    _FOOT_X0, _FOOT_X1 = HEEL - FOOT_L / 2, HEEL + FOOT_L / 2
    SHOE = _chaikin([(_FOOT_X0 - 0.5, _FOOT_BOT), (_FOOT_X1 + 0.5, _FOOT_BOT), (_FOOT_X1 + 1.6, _FOOT_BOT + 2.4),
                     (_FOOT_X1 + 0.4, _FOOT_BOT + 4.6), (_FOOT_X1 - 4.0, _FOOT_BOT + 6.0), (SHW / 2 + 3.5, _FOOT_BOT + 8.6),
                     (SHW / 2 + 0.8, _FOOT_BOT + 12.5), (-SHW / 2 - 1.0, _FOOT_BOT + 13.0), (_FOOT_X0 + 0.2, _FOOT_BOT + 9.5),
                     (_FOOT_X0 - 1.0, _FOOT_BOT + 3.0)])
    SOLE = [(x, min(y, _SOLE_TOP)) for x, y in SHOE]
    _FOOT_C = (HEEL, -SHH / 2 - FOOT_H / 2 + 1)
    _off_cache.clear()
JERSEY = _rrect(-TW / 2, -TH / 2, TW / 2, TH / 2, 7, 4)
SHORTS_BAND = _rrect(-TW / 2 - 0.6, -TH / 2 - 0.6, TW / 2 + 0.6, -TH / 2 + 13, 0, 4.6)
# 号码布：从躯干底边（短裤腰带上方）往上量，躯干长度变化时跟着走
BIB = _rrect(-6.5, -TH / 2 + 15, 6.5, -TH / 2 + 35, 2, 2, 2)
_BIB_C = (0, -TH / 2 + 25)
# 脚掌刚体中心在"小腿局部坐标"里的位置（站立、踝角 0 时）
_FOOT_C = (HEEL, -SHH / 2 - FOOT_H / 2 + 1)
FOOT_IDX = (9, 10)


def shoe_pose(pf):
    """脚掌刚体位姿 → 虚拟小腿坐标系位姿：在该坐标系里 SHOE/SOLE（小腿局部坐标）正好落在脚掌盒上。"""
    px, py, a = pf
    ca, sa = math.cos(a), math.sin(a)
    return (px - (_FOOT_C[0] * ca - _FOOT_C[1] * sa), py - (_FOOT_C[0] * sa + _FOOT_C[1] * ca), a)
_off_cache = {}


def _off(name, pts, d):
    key = (name, round(d, 2))
    r = _off_cache.get(key)
    if r is None:
        r = _off_cache[key] = _offset(pts, d)
    return r


# ------------------------------------------------------------ 调色板 ----
_pal_cache = {}


def _far(c):
    return mix(shade(c, 0.84), FAR_TINT, 0.18)


def palette(color, far, lane_i):
    key = (color, far, lane_i)
    p = _pal_cache.get(key)
    if p is None:
        p = dict(skin=SKIN, jersey=color, jersey_hi=mix(color, (255, 255, 255), 0.32), jersey_lo=shade(color, 0.80),
                 shorts=mix(shade(color, 0.42), (28, 30, 44), 0.35), sock=(246, 246, 250), stripe=color,
                 shoe=shade(color, 0.92), sole=(250, 250, 252), lace=(255, 255, 255), band=color,
                 hair=HAIR[lane_i % len(HAIR)], out=OUT, blush=BLUSH, bib=(252, 252, 250))
        if far:
            p = {n: (_far(c) if n != "out" else mix(OUT, FAR_TINT, 0.25)) for n, c in p.items()}
        p["skin_hi"] = mix(p["skin"], (255, 255, 255), 0.45)
        p["shorts_hi"] = mix(p["shorts"], (255, 255, 255), 0.18)
        p["jersey_hi2"] = mix(p["jersey"], (255, 255, 255), 0.22)
        _pal_cache[key] = p
    return p


# ------------------------------------------------------------ 绘制原语（屏幕坐标）
def _xf(v, pb):
    """刚体局部坐标(物理 px) → 屏幕 px 的变换函数。"""
    px, py, a = pb
    kk = v.scale / PPM
    ca, sa = math.cos(a) * kk, math.sin(a) * kk
    ox, oy = v.ax + px * kk, v.ay - py * kk
    return lambda lx, ly: (ox + lx * ca - ly * sa, oy - lx * sa - ly * ca)


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


def _piece(surf, a, b, r, col, cap_a, cap_b, ext, aa):
    """a→b 宽 2r 的条带；cap_* 为圆头，否则平头（平头沿轴向多伸出 ext，用于描边）。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    ln = math.hypot(dx, dy)
    if ln < 1e-6:
        _circle(surf, col, a, r, aa)
        return
    ux, uy = dx / ln, dy / ln
    nx, ny = -uy * r, ux * r
    ea = 0 if cap_a else ext
    eb = 0 if cap_b else ext
    ax, ay = a[0] - ux * ea, a[1] - uy * ea
    bx, by = b[0] + ux * eb, b[1] + uy * eb
    _poly(surf, col, [(ax + nx, ay + ny), (bx + nx, by + ny), (bx - nx, by - ny), (ax - nx, ay - ny)], aa)
    if cap_a:
        _circle(surf, col, a, r, aa)
    if cap_b:
        _circle(surf, col, b, r, aa)


def _seg(surf, a, b, r, fill, out, ow, cap_a=True, cap_b=True):
    """带统一粗细描边的胶囊/平头条带。"""
    if ow:
        _piece(surf, a, b, r + ow, out, cap_a, cap_b, ow, ow >= 2)
    _piece(surf, a, b, r, fill, cap_a, cap_b, 0, False)


def _shape(surf, f, pts, name, fill, out, ow, kk):
    """局部多边形（预计算）+ 外扩描边。"""
    if ow:
        _poly(surf, out, [f(x, y) for x, y in _off(name, pts, ow / kk)], ow >= 2)
    _poly(surf, fill, [f(x, y) for x, y in pts])


def _highlight(surf, a, b, r, col, lo=0.18, hi=0.82):
    """沿肢体一侧的高光细线：总在屏幕左上（光源方向）那一侧。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    ln = math.hypot(dx, dy)
    if ln < 4 or r < 3.2:
        return
    nx, ny = -dy / ln, dx / ln
    if nx * -0.6 + ny * -0.8 < 0:
        nx, ny = -nx, -ny
    o = r * 0.48
    p0 = (a[0] + dx * lo + nx * o, a[1] + dy * lo + ny * o)
    p1 = (a[0] + dx * hi + nx * o, a[1] + dy * hi + ny * o)
    pygame.draw.line(surf, col, p0, p1, max(1, int(r * 0.32)))


# ------------------------------------------------------------ 身体各部分
def _leg(surf, ft, fs, fsh, P, ow, kk, detail):
    """ft/fs：大腿/小腿局部→屏幕；fsh：由脚掌刚体反推的虚拟小腿坐标系（只用于画鞋）。"""
    hip, knee = ft(0, THH / 2), ft(0, -THH / 2)
    r = THW / 2 * kk
    _seg(surf, hip, knee, r, P["skin"], P["out"], ow)
    if detail:
        _highlight(surf, hip, knee, r, P["skin_hi"], 0.6, 0.9)
    # 短裤：大腿上 58%，略宽，平的裤脚（描边自然成裤脚线）
    hem = ft(0, THH / 2 - THH * 0.58)
    _seg(surf, hip, hem, r + 0.9 * kk, P["shorts"], P["out"], ow, cap_b=False)
    if detail:
        _highlight(surf, hip, hem, r + 0.9 * kk, P["shorts_hi"], 0.25, 0.95)
    # 小腿 + 袜子
    k2, ank = fs(0, SHH / 2), fs(0, -SHH / 2)
    rs = SHW / 2 * kk
    _seg(surf, k2, ank, rs, P["skin"], P["out"], ow)
    if detail:
        _highlight(surf, k2, ank, rs, P["skin_hi"], 0.15, 0.55)
    sock_top = fs(0, -SHH / 2 + 15)
    _seg(surf, sock_top, ank, rs + 0.4 * kk, P["sock"], P["out"], ow, cap_a=False, cap_b=False)
    if rs >= 2.5:
        a, b = fs(0, -SHH / 2 + 12.5), fs(0, -SHH / 2 + 10)
        _piece(surf, a, b, rs + 0.4 * kk, P["stripe"], False, False, 0, False)
    # 跑鞋（跟着脚掌刚体走）
    fs = fsh
    _shape(surf, fs, SHOE, "shoe", P["shoe"], P["out"], ow, kk)
    _poly(surf, P["sole"], [fs(x, y) for x, y in SOLE])
    pygame.draw.line(surf, P["out"], fs(_FOOT_X0 - 0.6, _SOLE_TOP), fs(_FOOT_X1 + 0.9, _SOLE_TOP), 1)
    if detail:          # 鞋带 + 侧面白条
        for j in range(3):
            x = SHW / 2 + 2.2 + j * 2.8            # 鞋带沿鞋面（踝前）排开
            y = _FOOT_BOT + 9.8 - j * 1.1
            pygame.draw.line(surf, P["lace"], fs(x - 1.3, y - 1.4), fs(x + 1.3, y + 1.0), max(1, int(kk * 1.3)))
        pygame.draw.line(surf, P["lace"], fs(_FOOT_X0 + 1.5, _SOLE_TOP + 1.6), fs(SHW / 2 + 1, _SOLE_TOP + 3.6), max(1, int(kk * 1.2)))


def _arm(surf, fa, ff, P, ow, kk, detail):
    sh, el = fa(0, AH / 2), fa(0, -AH / 2)
    r = AW / 2 * kk
    _seg(surf, sh, el, r, P["skin"], P["out"], ow)
    sleeve = fa(0, AH / 2 - AH * 0.45)
    _seg(surf, sh, sleeve, r + 0.8 * kk, P["jersey"], P["out"], ow, cap_b=False)
    if detail:
        _highlight(surf, sh, sleeve, r + 0.8 * kk, P["jersey_hi2"], 0.2, 0.95)
        _highlight(surf, sleeve, el, r, P["skin_hi"], 0.2, 0.8)
    # 前臂（含手）：手 = 末端小圆，底边与物理前臂末端齐平
    e2 = ff(0, FH / 2)
    hr = FW / 2 * 1.2
    hand = ff(0, -FH / 2 + hr + 0.2)
    rf = FW / 2 * kk
    _seg(surf, e2, hand, rf, P["skin"], P["out"], ow)
    if detail:
        _highlight(surf, e2, hand, rf, P["skin_hi"], 0.15, 0.6)
    if rf >= 2:     # 护腕
        _seg(surf, ff(0, -FH / 2 + 15), ff(0, -FH / 2 + 10.5), rf + 0.6 * kk, P["band"], P["out"], max(1, ow - 1), False, False)
    _circle(surf, P["out"], hand, hr * kk + ow, ow >= 2)
    _circle(surf, P["skin"], hand, hr * kk)


_rot_cache = {}


def _bib_text(label, size, col, deg):
    key = (label, size, col, deg)
    img = _rot_cache.get(key)
    if img is None:
        if len(_rot_cache) > 2000:
            _rot_cache.clear()
        img = text(label, size, col, True)
        if deg:
            img = pygame.transform.rotate(img, deg)
        _rot_cache[key] = img
    return img


def _torso(surf, f, a, P, ow, kk, detail, label):
    # 脖子（躯干之前画，被球衣和头压住两端）
    _seg(surf, f(0, TH / 2 - 4), f(0, TH / 2 + 3), 3.6 * kk, P["skin"], P["out"], ow)
    _shape(surf, f, JERSEY, "jersey", P["jersey"], P["out"], ow, kk)
    if detail:
        # 光从屏幕左上来：亮的一侧画高光条，另一侧画暗部
        lit = 1 if (-0.6 * math.cos(a) + 0.8 * math.sin(a)) > 0 else -1
        x_hi, x_lo = 5.0 * lit, -5.0 * lit
        _poly(surf, P["jersey_lo"], [f(x_lo, -TH / 2 + 12), f(x_lo - 4.2 * lit, -TH / 2 + 12),
                                     f(x_lo - 4.2 * lit, TH / 2 - 7), f(x_lo, TH / 2 - 3)])
        _poly(surf, P["jersey_hi"], [f(x_hi, -TH / 2 + 14), f(x_hi + 2.2 * lit, -TH / 2 + 14),
                                     f(x_hi + 2.2 * lit, TH / 2 - 8), f(x_hi, TH / 2 - 6)])
        # 领口（白色滚边）
        pygame.draw.line(surf, P["bib"], f(-6, TH / 2 - 1.2), f(6, TH / 2 - 1.2), max(1, int(kk * 1.6)))
    # 短裤腰部
    _shape(surf, f, SHORTS_BAND, "band", P["shorts"], P["out"], ow, kk)
    # 号码布 + 选手缩写
    if detail and label:
        _shape(surf, f, BIB, "bib", P["bib"], P["out"], 1, kk)
        size = int(13 * kk)
        img = text(label, size, P["jersey_lo"], True)
        while size > 7 and img.get_width() > 12 * kk:
            size -= 1
            img = text(label, size, P["jersey_lo"], True)
        deg = int(round(math.degrees(a) / 4.0)) * 4 % 360
        img = _bib_text(label, size, P["jersey_lo"], deg)
        cx, cy = f(*_BIB_C)
        surf.blit(img, (cx - img.get_width() / 2, cy - img.get_height() / 2))


def _head(surf, c, R, a, P, ow, expr, k, look):
    ca, sa = math.cos(a), math.sin(a)

    def L(lx, ly):           # 头部局部坐标（以 R 为单位，+x 朝前，+y 朝上）→ 屏幕
        return (c[0] + (lx * ca - ly * sa) * R, c[1] - (lx * sa + ly * ca) * R)

    aa = ow >= 2
    nose = L(1.0, -0.06)
    rn = max(1.0, R * 0.19)
    _circle(surf, P["out"], c, R + ow, aa)
    if R >= 5:
        _circle(surf, P["out"], nose, rn + ow, False)
    _circle(surf, P["skin"], c, R)
    if R >= 5:
        _circle(surf, P["skin"], nose, rn)
    if R < 3.5:
        pygame.draw.circle(surf, INK, L(0.45, 0.2), 1)
        return
    # 头发：后上半边 + 额前一撮
    hair = [L(math.cos(t), math.sin(t)) for t in (math.radians(d) for d in range(66, 214, 12))]
    hair += [L(-0.62, -0.12), L(-0.5, 0.3), L(-0.12, 0.5), L(0.3, 0.56), L(0.5, 0.7), L(0.62, 0.62), L(0.5, 0.84)]
    pygame.draw.polygon(surf, P["hair"], hair)
    if R >= 7:
        pygame.draw.lines(surf, mix(P["hair"], (255, 255, 255), 0.3), False, [L(-0.55, 0.62), L(-0.2, 0.82), L(0.12, 0.86)], max(1, int(R * 0.1)))
    # 头带（赛道色）+ 飘动的结
    bw = max(2, int(R * 0.26))
    pygame.draw.line(surf, P["band"], L(-0.8, 0.6), L(0.8, 0.6), bw)
    if R >= 5:
        wv = 0.12 * math.sin(k * 0.4)
        for j, (dy, ln) in enumerate(((0.1, 1.45), (-0.08, 1.3))):
            pygame.draw.line(surf, P["band"], L(-0.82, 0.6), L(-ln, 0.6 + dy + wv * (1 + j)), max(1, bw - 1))
    # 耳朵
    if R >= 6:
        e = L(-0.24, -0.02)
        pygame.draw.circle(surf, P["out"], e, R * 0.25 + 1)
        pygame.draw.circle(surf, P["skin"], e, R * 0.25)
        pygame.draw.circle(surf, shade(P["skin"], 0.82), e, max(1, R * 0.11))
    _face(surf, L, R, expr, k, look, P)


def _face(surf, L, R, expr, k, look, P):
    lw = max(1, int(R * 0.13))
    eyes = (L(0.32, 0.12), L(0.70, 0.12))
    if expr in ("normal", "grin") and R >= 6:
        pygame.draw.circle(surf, P["blush"], L(0.4, -0.2), R * 0.14)
    if expr == "dizzy":
        for j, (ex, ey) in enumerate(eyes):
            pts = []
            for q in range(14):
                tt = q / 13 * 4 * math.pi
                rr = R * (0.05 + 0.19 * q / 13)
                aa = tt + k * 0.25 * (1 if j else -1)
                pts.append((ex + math.cos(aa) * rr, ey + math.sin(aa) * rr))
            pygame.draw.lines(surf, INK, False, pts, 1)
        pygame.draw.lines(surf, INK, False, [L(0.26 + 0.11 * q, -0.44 + (0.07 if q % 2 else -0.04)) for q in range(6)], lw)
        if R >= 5:                   # 头顶转圈的小星星
            for j in range(3):
                aa = k * 0.12 + j * 2.094
                sx, sy = L(0, 0)[0] + math.cos(aa) * R * 1.5, L(0, 0)[1] - R * 1.35 + math.sin(aa) * R * 0.45
                rs = max(2, R * 0.32)
                star = [(sx + math.cos(i * math.pi / 5 - 1.57) * (rs if i % 2 == 0 else rs * 0.45),
                         sy + math.sin(i * math.pi / 5 - 1.57) * (rs if i % 2 == 0 else rs * 0.45)) for i in range(10)]
                pygame.draw.polygon(surf, (255, 215, 40), star)
                pygame.draw.polygon(surf, (190, 130, 20), star, 1)
        return
    if expr == "grin":
        for ex, ey in eyes:
            pygame.draw.lines(surf, INK, False, [(ex - R * 0.15, ey + R * 0.05), (ex, ey - R * 0.11), (ex + R * 0.15, ey + R * 0.05)], lw)
        mouth = [L(0.55 + 0.32 * math.cos(t), -0.24 + 0.32 * math.sin(t)) for t in [math.pi + q * math.pi / 8 for q in range(9)]]
        pygame.draw.polygon(surf, MOUTH, mouth)
        if R >= 7:
            pygame.draw.circle(surf, (235, 110, 120), L(0.55, -0.46), R * 0.13)
        pygame.draw.line(surf, (255, 255, 255), mouth[0], mouth[-1], max(1, lw))
        pygame.draw.polygon(surf, INK, mouth, 1)
        return
    # normal / surprised：白眼球 + 瞳孔（看运动方向）
    big = expr == "surprised"
    rw = max(1.5, R * (0.26 if big else 0.22))
    rp = max(1.0, R * (0.10 if big else 0.12))
    lx, ly = look
    off = R * (0.04 if big else 0.07)
    for j, (ex, ey) in enumerate(eyes):
        s = 0.88 if j == 0 else 1.0           # 后面那只眼略小（3/4 侧脸）
        if R >= 6:
            pygame.draw.circle(surf, (255, 255, 255), (ex, ey), rw * s)
            pygame.draw.circle(surf, INK, (ex, ey), rw * s, 1)
            pygame.draw.circle(surf, INK, (ex + lx * off, ey + ly * off), rp * s)
        else:
            pygame.draw.circle(surf, INK, (ex, ey), max(1, R * 0.13))
    if R >= 6:      # 眉毛：平时略压低（专注），惊讶时挑高
        for ex in (0.32, 0.70):
            if big:
                pygame.draw.lines(surf, INK, False, [L(ex - 0.13, 0.36), L(ex, 0.45), L(ex + 0.13, 0.38)], lw)
            else:
                pygame.draw.line(surf, INK, L(ex - 0.13, 0.37), L(ex + 0.13, 0.32), lw)
    if big:
        m = L(0.6, -0.42)
        pygame.draw.circle(surf, MOUTH, m, max(2, R * 0.16))
        pygame.draw.circle(surf, INK, m, max(2, R * 0.16), 1)
        if R >= 8:      # 汗滴
            d = L(-0.05, 0.3)
            pygame.draw.circle(surf, (120, 190, 250), (d[0], d[1] + R * 0.1), R * 0.12)
            pygame.draw.polygon(surf, (120, 190, 250), [(d[0] - R * 0.11, d[1] + R * 0.08), (d[0] + R * 0.11, d[1] + R * 0.08), (d[0], d[1] - R * 0.14)])
    else:
        pygame.draw.lines(surf, INK, False, [L(0.36, -0.36), L(0.56, -0.48), L(0.76, -0.38)], lw)


# ------------------------------------------------------------ 入口 --------
def outline_width(scale):
    return 1 if scale < 85 else 2 if scale < 150 else 3


def draw_runner(surf, v, pose, color, expr, k, lane_i=0, label="", look=None):
    kk = v.scale / PPM
    ow = outline_width(v.scale)
    detail = v.scale >= 80
    head = None
    a0 = pose[0][2]
    # 视线：朝前方 + 运动方向的混合（屏幕坐标，y 向下）
    fx_, fy_ = math.cos(a0), math.sin(a0)
    if look is not None:
        fx_, fy_ = fx_ * 0.8 + look[0] * 0.5, fy_ * 0.8 + look[1] * 0.5
    ln = math.hypot(fx_, fy_) or 1.0
    look_s = (fx_ / ln, -fy_ / ln)
    for b, far in LAYERS:
        P = palette(color, far, lane_i)
        if b == 0:
            f = _xf(v, pose[0])
            _torso(surf, f, a0, P, ow, kk, detail, label)
            c = f(0, TH / 2 + HR)
            R = HR * kk
            _head(surf, c, R, a0, P, ow, expr, k, look_s)
            head = (c, R)
        elif b in (2, 4):
            foot = pose[FOOT_IDX[0] if b == 2 else FOOT_IDX[1]]
            _leg(surf, _xf(v, pose[b - 1]), _xf(v, pose[b]), _xf(v, shoe_pose(foot)), P, ow, kk, detail)
        elif b in (6, 8):
            _arm(surf, _xf(v, pose[b - 1]), _xf(v, pose[b]), P, ow, kk, detail)
    return head
