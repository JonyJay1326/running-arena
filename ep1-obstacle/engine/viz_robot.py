"""--body-style robot：Opus A 的 CRT 电视头 + 工业风骨架 + 铁皮玩具细节（波纹软管、铆钉、发条钥匙、颈圈）。
纯渲染，不碰物理/计分/随机数。接口同 viz_body2.draw_runner；头像嵌进电视屏幕用 draw_screen_avatar（visualizer._draw_avatar 在 robot 样式下调用）。
身体 = viz_robot_ind（工业风骨架）+ viz_robot_tv（Opus 设计的 CRT 电视头 / 波纹软管 / 铆钉 / 发条钥匙）。"""
import math
import random
import pygame
import viz_runner as vr, viz_fx, viz_body2 as b2
from viz_fx import pf, px, mix, shade, text
import viz_robot_tv as body_a, viz_robot_ind as b3

PPM = vr.PPM; TW, TH, HR = vr.TW, vr.TH, vr.HR
THH, SHH, AH, FH = vr.THH, vr.SHH, vr.AH, vr.FH
_pal = {}
TIN_KEYS = ("tin", "tin_hi", "tin_lo", "tin_dk", "brass", "shell_dk")
NO_FACE = False
KEY_PER_PX = 0.045       # 发条钥匙转角 = 位移 × 此系数（像素→弧度，像轮子一样随跑动转，站着不动只慢慢转）
KEY_IDLE = 0.03          # 静止时每帧的微转（弧度/帧）
HEAD_SCALE = 1.4         # 电视头比物理头大一圈（纯视觉），给屏幕里的头像留空间
ANT_SMALL = 0.3          # 叠放时天线缩短
HEAD_SCALE_SMALL = 2.0   # 叠放（v.scale 小）时再放大，让屏幕里的头像看得清


def palette(color, far, lane_i):
    key = (color, far, lane_i)
    P = vr.palette(color, far, lane_i)
    q = _pal.get(key)
    if q is None or q[0] is not P:
        Qb = dict(b3.palette(color, far, lane_i))
        Qa = body_a.palette(color, far, lane_i)
        for k in TIN_KEYS:
            Qb[k] = Qa[k]
        Qb["acc"] = Qa["acc"]
        Qb["m_dk"] = shade(Qb["m_dk"], 1.25)
        q = _pal[key] = (P, Qb, Qa)
    return q[1], q[2]


def _rivets(surf, f, pts, r, Q):
    for x, y in pts:
        body_a._rivet(surf, f(x, y), r, Q)


_PLATE_T = b3._chamfer(0.6, -15.0, 7.4, 14.0, 3.0)
_PLATE_S = [(-0.6, -19.0), (6.4, -19.0), (6.0, 14.0), (3.0, 20.0), (-0.6, 20.0)]
_PLATE_FORE = b3._chamfer(-3.6, -13.0, 4.4, 15.0, 2.2)


def _leg(surf, ft, fs, fsh, Q, ow, kk, detail, cel):
    hip, knee = ft(0, THH / 2), ft(0, -THH / 2)
    k2, ank = fs(0, SHH / 2), fs(0, -SHH / 2)
    km = b2._lerp(knee, k2, 0.5)
    aa = ow >= 2
    rt, rs = 4.3 * kk, 3.6 * kk
    if ow:
        b2._caps(surf, Q["out"], km, ank, rs, rs, ow, aa)
        vr._poly(surf, Q["out"], [fsh(x, y) for x, y in vr._off("shoe", vr.SHOE, ow / kk)], aa)
    body_a._hose(surf, hip, km, rt, Q, ow, kk, detail)             # 大腿：银色波纹软管
    b2._caps(surf, Q["tin_lo"], km, ank, rs, rs)                   # 小腿：浅钢骨架
    if kk * 2 >= pf(1.5):
        b3._piston(surf, fs(-4.4, SHH / 2 - 4), fs(-4.0, -SHH / 2 + 8), Q, 0, kk, 1.1)
    b3._shape(surf, ft, _PLATE_T, Q["shell"], Q["out"], ow, kk)
    b3._shape(surf, fs, _PLATE_S, Q["shell"], Q["out"], ow, kk)
    pygame.draw.line(surf, Q["acc"], ft(1.0, 3.0), ft(7.0, 3.0), max(1, int(kk * 1.4)))
    pygame.draw.line(surf, Q["acc"], fs(0.2, -4.0), fs(5.8, -4.0), max(1, int(kk * 1.4)))
    if detail:
        _rivets(surf, ft, [(4.0, 11.0), (4.0, -12.0)], max(1.2, 1.1 * kk), Q)
        _rivets(surf, fs, [(3.0, 11.0), (3.0, -15.5)], max(1.2, 1.1 * kk), Q)
    b3._foot_ind(surf, fsh, Q, ow, kk, detail)
    body_a._hinge(surf, km, max(pf(3.4), 5.2 * kk), Q, ow, detail)
    body_a._hinge(surf, hip, max(pf(3.8), 5.8 * kk), Q, ow, detail)
    b3._bolt(surf, ank, max(pf(2.4), 3.4 * kk), Q, ow, detail, ring=Q["tin_lo"])


def _arm(surf, fa, ff, Q, ow, kk, detail, cel):
    sh, el = fa(0, AH / 2), fa(0, -AH / 2)
    e2, wr = ff(0, FH / 2), ff(0, -FH / 2 + 5)
    em = b2._lerp(el, e2, 0.5)
    hand = ff(0, -FH / 2 + 3.8)
    aa = ow >= 2
    if ow:
        b2._caps(surf, Q["out"], em, wr, 3.0 * kk, 3.0 * kk, ow, aa)
        pygame.draw.circle(surf, Q["out"], hand, 4.2 * kk + ow)
    body_a._hose(surf, sh, em, 3.6 * kk, Q, ow, kk, detail)
    b2._caps(surf, Q["tin_lo"], em, wr, 3.0 * kk, 3.0 * kk)
    b3._shape(surf, ff, _PLATE_FORE, Q["shell"], Q["out"], ow, kk)
    hr = 4.2 * kk
    pygame.draw.circle(surf, Q["m_dk"], hand, hr)
    if hr >= pf(3):
        pygame.draw.circle(surf, Q["tin_lo"], (hand[0] - hr * 0.18, hand[1] - hr * 0.2), hr * 0.5)
    pygame.draw.line(surf, Q["acc"], ff(-3.4, -9.0), ff(4.0, -9.0), max(1, int(kk * 1.3)))
    body_a._hinge(surf, em, max(pf(2.8), 4.0 * kk), Q, ow, detail)
    body_a._hinge(surf, sh, max(pf(3.4), 5.0 * kk), Q, ow, detail)


_PACK = b3._chamfer(-vr.TW / 2 - 8.0, -TH / 2 + 12, -vr.TW / 2 + 1.0, TH / 2 - 22, 2.2)


def _torso(surf, f, a, Q, Qa, ow, kk, detail, label, cel, k):
    aa = ow >= 2
    body_a._key(surf, f, a, Qa, ow, kk, k)
    b3._shape(surf, f, _PACK, Q["tin_lo"], Q["out"], ow, kk, aa)
    if detail:
        for j in range(3):
            pygame.draw.line(surf, Q["tin_dk"], f(-TW / 2 - 6.4, -TH / 2 + 17 + j * 5), f(-TW / 2 - 1.6, -TH / 2 + 17 + j * 5), 1)
    b3._shape(surf, f, b3._TORSO_I, Q["m_dk"], Q["out"], ow, kk, aa)
    b3._shape(surf, f, b3._CHEST_I, Q["shell"], Q["out"], ow, kk, aa)
    lit = 1 if (-0.6 * math.cos(a) + 0.8 * math.sin(a)) > 0 else -1
    if cel:
        band = b2._clip_half(b3._CHEST_I, -lit, TW / 2 - 3.4)
        if len(band) >= 3:
            vr._poly(surf, Q["shell_lo"], [f(x, y) for x, y in band])
    if kk * 2 >= pf(1.2):
        pygame.draw.line(surf, Q["acc"], f(-TW / 2 + 0.6, -TH / 2 + 30), f(TW / 2 + 0.2, -TH / 2 + 30), max(1, int(kk * 1.6)))
    if detail:
        for j in range(3):
            pygame.draw.line(surf, Q["tin_lo"], f(-4.5, -TH / 2 + 12 + j * 3), f(4.5, -TH / 2 + 12 + j * 3), max(1, int(kk * 1.0)))
        _rivets(surf, f, [(-7.2, -TH / 2 + 24.5), (7.6, -TH / 2 + 24.5), (-7.2, TH / 2 - 4), (7.6, TH / 2 - 4)], max(1.2, 1.1 * kk), Q)
        if label:
            size = int(11 * kk)
            while size > px(7) and text(label, size, (255, 255, 255), True).get_width() > 11 * kk:
                size -= 1
            img = vr._bib_text(label, size, (255, 255, 255), int(round(math.degrees(a) / 4.0)) * 4 % 360)
            cx, cy = f(0.4, -TH / 2 + 40)
            surf.blit(img, (cx - img.get_width() / 2, cy - img.get_height() / 2))
    b3._shape(surf, f, b3._PELVIS_I, Q["m_dk"], Q["out"], ow, kk, aa)
    if detail:
        pygame.draw.circle(surf, Q["glow"], f(0.4, -TH / 2 + 4.5), max(1, 1.5 * kk))
    body_a._hose(surf, f(0, TH / 2 - 2), f(0, TH / 2 + 3.5), 3.4 * kk, Q, ow, kk, False)      # 颈部
    cw = f(-5.4, TH / 2 - 0.8), f(5.4, TH / 2 - 0.8)                                           # 颈圈
    pygame.draw.line(surf, Q["out"], cw[0], cw[1], max(2, int(3.0 * kk)) + 2 * ow)
    pygame.draw.line(surf, Q["tin"], cw[0], cw[1], max(2, int(3.0 * kk)))


def draw_runner(surf, v, pose, color, expr, k, lane_i=0, label="", look=None):
    kk = v.scale / PPM
    ow = vr.outline_width(v.scale) if viz_fx.S == 1.0 else px(vr.outline_width(v.scale / viz_fx.S))
    detail = v.scale >= pf(80)
    cel = v.scale >= 70
    a0 = pose[0][2]
    fx_, fy_ = math.cos(a0), math.sin(a0)
    if look is not None:
        fx_, fy_ = fx_ * 0.8 + look[0] * 0.5, fy_ * 0.8 + look[1] * 0.5
    ln = math.hypot(fx_, fy_) or 1.0
    sx, sy = fx_ / ln, fy_ / ln
    look_l = (sx * math.cos(a0) + sy * math.sin(a0) - 1.0, -sx * math.sin(a0) + sy * math.cos(a0))
    head = None
    for b, far in vr.LAYERS:
        Q, Qa = palette(color, far, lane_i)
        if b == 0:
            f = vr._xf(v, pose[0])
            _torso(surf, f, a0, Q, Qa, ow, kk, detail, label, cel, (pose[0][0] * KEY_PER_PX + k * KEY_IDLE) / 0.18)
            hs = HEAD_SCALE if detail else HEAD_SCALE_SMALL
            body_a.ANT = 1.0 if detail else ANT_SMALL
            R = HR * kk * hs
            c = f(0, TH / 2 + 0.98 * HR * hs - 1.0)
            body_a._head(surf, c, R, a0, Qa, ow, "blank" if NO_FACE else expr, k, (look_l[0] * 2.0, -look_l[1] * 2.0), detail)
            head = (c, R)
        elif b in (2, 4):
            foot = pose[vr.FOOT_IDX[0] if b == 2 else vr.FOOT_IDX[1]]
            _leg(surf, vr._xf(v, pose[b - 1]), vr._xf(v, pose[b]), vr._xf(v, vr.shoe_pose(foot)), Q, ow, kk, detail, cel)
        elif b in (6, 8):
            _arm(surf, vr._xf(v, pose[b - 1]), vr._xf(v, pose[b]), Q, ow, kk, detail, cel)
    return head


# ---------------------------------------------------------------- 头像嵌进电视屏幕
SCR = body_a._SCR          # 屏幕在头部局部坐标里的矩形（单位 R）
BOOT = None               # 外部（入场镜头）每帧设 0~1 触发 CRT 开机动画；None = 不开机，画完设回 None
SCREEN_OVERRIDE = None     # 外部（转播 HUD）可临时设成 "foul"（红色 FOUL）/ "win"（星星眼），画完一帧记得清回 None
_rm_cache = {}


def _round_mask(w, h, r):
    key = (w, h, r)
    m = _rm_cache.get(key)
    if m is None:
        m = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(m, (255, 255, 255, 255), m.get_rect(), border_radius=r)
        _rm_cache[key] = m
    return m


def _star4(surf, c, s, col):
    pts = [(c[0], c[1] - s), (c[0] + s * 0.3, c[1] - s * 0.3), (c[0] + s, c[1]), (c[0] + s * 0.3, c[1] + s * 0.3),
           (c[0], c[1] + s), (c[0] - s * 0.3, c[1] + s * 0.3), (c[0] - s, c[1]), (c[0] - s * 0.3, c[1] - s * 0.3)]
    pygame.draw.polygon(surf, col, pts)


def draw_screen_avatar(surf, ch, head, a, expr="normal", k=0, boot=None):
    """把角色头像画进电视屏幕（圆角裁剪 + 淡扫描线 + 玻璃反光），随头部旋转。head = ((cx, cy), R)，a = 躯干角。
    屏幕状态（纯由 expr / k 决定，随便回放拖动都稳定）：
      dizzy（摔倒晕眩）→ 信号雪花 + 横条错位抖动 + 偏蓝；  grin / SCREEN_OVERRIDE="win" → 金色星星眼；
      SCREEN_OVERRIDE="foul" → 红色 FOUL；boot（0~1，入场用）→ CRT 开机：白线展开 → 竖向展开 → 白光褪去。"""
    if boot is None:
        boot = BOOT
    (cx, cy), R = head
    ca, sa = math.cos(a), math.sin(a)
    x0, y0, x1, y1 = SCR
    w, h = int((x1 - x0) * R), int((y1 - y0) * R)
    if w < 4 or h < 4:
        return
    rad = max(2, int(R * 0.26))
    iw, ih = max(4, int(w * 0.94)), max(4, int(h * 0.92))
    pad = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(pad, (22, 40, 34), pad.get_rect(), border_radius=rad)
    S = int(ih * 1.45)                    # 脸心在图中心下 0.17S → 把图往上提
    img = pygame.transform.smoothscale(ch.head, (S, S))
    layer = pygame.Surface((w, h), pygame.SRCALPHA)
    layer.blit(img, ((w - S) // 2, int(h / 2 - S / 2 - 0.17 * S)))
    mask = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), pygame.Rect((w - iw) // 2, (h - ih) // 2, iw, ih), border_radius=max(2, int(R * 0.22)))
    layer.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    pad.blit(layer, (0, 0))
    kk_ = int(k)
    state = SCREEN_OVERRIDE or ("dizzy" if expr == "dizzy" else "win" if expr == "grin" else None)
    rmask = _round_mask(w, h, rad)
    if state == "dizzy":                  # 横条错位：把画面切成几条，各自左右抖
        g = pygame.Surface((w, h), pygame.SRCALPHA)
        g.fill((22, 40, 34, 255))
        nb = 6
        for i in range(nb):
            y_a, y_b = h * i // nb, h * (i + 1) // nb
            off = int(math.sin(kk_ * 0.9 + i * 2.3) * R * 0.14 * (1 if (kk_ // 3 + i) % 3 else 0.2))
            g.blit(pad, (off, y_a), pygame.Rect(0, y_a, w, y_b - y_a))
        g.blit(rmask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        pad = g
        tint = pygame.Surface((w, h), pygame.SRCALPHA)
        tint.fill((10, 30, 70, 0))
        pad.blit(tint, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
    ov = pygame.Surface((w, h), pygame.SRCALPHA)       # 扫描线 / 反光 / 雪花画在单独图层再叠（直接画会覆盖像素）
    if R >= 14:
        for yy in range((h - ih) // 2, (h + ih) // 2, 4):
            pygame.draw.line(ov, (0, 20, 14, 30), ((w - iw) // 2, yy), ((w + iw) // 2, yy))
    if R >= 6:
        pygame.draw.lines(ov, (200, 235, 220, 110), False, [(w * 0.12, h * 0.55), (w * 0.14, h * 0.80), (w * 0.28, h * 0.86)], max(1, int(R * 0.08)))
    if state == "dizzy" and R >= 6:
        rng = random.Random(kk_ // 2 * 7919 + 17)
        cell = max(1, int(R * 0.09))
        for _ in range(int(w * h / (cell * cell) * 0.18)):
            v = rng.choice((235, 190, 120, 60))
            pygame.draw.rect(ov, (v, v, v, rng.choice((150, 220))), (rng.randrange(w), rng.randrange(h), cell, cell))
    if state == "win" and R >= 6:
        for ex in (0.34, 0.66):
            s_ = R * (0.34 + 0.06 * math.sin(kk_ * 0.3 + ex * 9))
            pygame.draw.circle(ov, (30, 30, 30, 150), (w * ex, h * 0.45), s_ * 0.9)
            _star4(ov, (w * ex, h * 0.45), s_, (255, 216, 60, 255))
    if state == "foul":
        red = pygame.Surface((w, h), pygame.SRCALPHA)
        red.fill((200, 20, 20, 150 if (kk_ // 8) % 2 == 0 else 95))
        ov.blit(red, (0, 0))
        if R >= 8:
            fs_ = max(8, int(h * 0.42))
            img_t = text("FOUL", fs_, (255, 255, 255), True)
            while fs_ > 6 and img_t.get_width() > w * 0.92:
                fs_ -= 1
                img_t = text("FOUL", fs_, (255, 255, 255), True)
            ov.blit(img_t, ((w - img_t.get_width()) // 2, (h - img_t.get_height()) // 2))
    pad.blit(ov, (0, 0))
    if boot is not None and boot < 1.0:   # CRT 开机
        g = pygame.Surface((w, h), pygame.SRCALPHA)
        if boot < 0.35:                    # 一条白线从中心向两边展开
            lw = max(2, int(w * (boot / 0.35)))
            pygame.draw.rect(g, (255, 255, 255, 255), ((w - lw) // 2, h // 2 - 1, lw, max(2, int(R * 0.1))))
        else:
            u = min(1.0, (boot - 0.35) / 0.25)           # 竖向展开
            lh = max(2, int(h * u))
            part = pygame.Surface((w, lh), pygame.SRCALPHA)
            part.blit(pad, (0, 0), pygame.Rect(0, (h - lh) // 2, w, lh))
            g.blit(part, (0, (h - lh) // 2))
            glow = max(0.0, 1.0 - (boot - 0.35) / 0.65)   # 白光褪去
            if glow > 0:
                wh = pygame.Surface((w, lh), pygame.SRCALPHA)
                gv = int(200 * glow)
                wh.fill((gv, gv, gv, 0))
                g.blit(wh, (0, (h - lh) // 2), special_flags=pygame.BLEND_RGB_ADD)
        g.blit(rmask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        base = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(base, (10, 16, 14), base.get_rect(), border_radius=rad)
        base.blit(g, (0, 0))
        pad = base
    img2 = pygame.transform.rotate(pad, math.degrees(a))
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    surf.blit(img2, (int(cx + (mx * ca - my * sa) * R - img2.get_width() / 2),
                     int(cy - (mx * sa + my * ca) * R - img2.get_height() / 2)))
