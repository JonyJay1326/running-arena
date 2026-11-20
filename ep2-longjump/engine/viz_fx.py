"""visualizer 的美术素材与特效（纯装饰，不参与模拟，也不碰全局 random）。

- 背景：天空渐变（按赛道色轻微着色）+ 两层视差（远山/云/星空、看台/环形山），按 (尺寸, 模式) 缓存
- 地面：按世界坐标切成 1024px 宽的分块，第一次用到时画好并缓存（草皮条纹、土层纹理、刻度、路牌、减速带、终点拱门）
- 小人装饰：脸（正常/晕眩/咧嘴/惊讶）、头带、球鞋、关节点、阴影、语音气泡与小图标
- 特效：载入时用 random.Random(12345) 从录制好的逐帧数据预生成全部事件与粒子参数，
  渲染时只依赖回放位置 k（可为小数帧号，慢动作插值用）→ 暂停/逐帧/拖动/重播都完全确定
"""
import bisect
import math
import os
import random
from collections import OrderedDict

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402

import evaluator  # noqa: E402
import physics  # noqa: E402

PPM, FPS = physics.PPM, physics.FPS
FX_SEED = 12345
KEY = (255, 0, 255)               # 分块/视差层的透明色
INK = (30, 34, 40)
DOWN_RED = (210, 40, 40)
GOLD = (250, 205, 70)
# 录制位姿 poses[k] 的刚体顺序（visualizer._body_order）：0 躯干，1/2 左大腿/小腿，3/4 右大腿/小腿，
# 5/6 左上臂/前臂，7/8 右上臂/前臂，9/10 左/右脚掌，之后是栏架
FOOT_IDX = (9, 10)
HURDLE0 = 11

# 每种模式的配色
STYLE = {
    "normal": dict(sky_top=(118, 178, 236), sky_bot=(226, 240, 252), tint=0.10,
                   grass=((116, 186, 84), (100, 170, 72)), grass_edge=(66, 128, 52), tuft=(80, 150, 60),
                   soil=(168, 126, 86), soil_dark=(140, 102, 68), soil_light=(192, 152, 110),
                   label=(255, 244, 226), label_shadow=(92, 62, 36), dust=(246, 238, 222),
                   hills=((184, 210, 186), (156, 196, 160)), stand=(196, 204, 220), stand_line=(176, 186, 204),
                   ads=((236, 96, 84), (70, 130, 220), (250, 196, 60), (60, 170, 110))),
    "ice": dict(sky_top=(150, 196, 236), sky_bot=(236, 246, 254), tint=0.08,
                grass=((214, 236, 250), (196, 224, 244)), grass_edge=(140, 186, 224), tuft=None,
                soil=(150, 182, 214), soil_dark=(126, 158, 194), soil_light=(184, 210, 234),
                label=(250, 253, 255), label_shadow=(70, 100, 140), dust=(244, 250, 255),
                hills=((200, 214, 232), (176, 196, 222)), stand=(206, 216, 232), stand_line=(186, 198, 218),
                ads=((120, 170, 230), (80, 120, 200), (230, 240, 250), (150, 200, 240)), sheen=True),
    "moon": dict(sky_top=(5, 7, 20), sky_bot=(30, 34, 64), tint=0.06,
                 grass=((156, 156, 166), (142, 142, 152)), grass_edge=(104, 104, 114), tuft=None,
                 soil=(96, 96, 106), soil_dark=(74, 74, 84), soil_light=(124, 124, 134),
                 label=(236, 238, 246), label_shadow=(30, 30, 40), dust=(190, 190, 200),
                 hills=((58, 60, 80), (78, 80, 98)), stand=(88, 90, 108), stand_line=(70, 72, 88),
                 ads=((120, 120, 140),), stars=True),
}
MODE_LABEL = {"normal": "标准重力", "ice": "冰面", "moon": "月球重力"}
ZONE_COLORS = {"助跑道": (70, 170, 90), "沙坑": (214, 170, 80), "平地": (70, 170, 90), "减速带": (235, 180, 30), "上坡": (230, 120, 40),
               "下坡": (60, 130, 220), "跨栏": (220, 60, 60)}
CONFETTI = [(240, 70, 70), (250, 200, 60), (70, 150, 240), (80, 200, 120), (240, 130, 220), (255, 255, 255)]
GRASS_TH = 0.07                    # 草皮厚度 (m)


def shade(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c)


def mix(a, b, t):
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))


# ------------------------------------------------------------ 字体/文字 ----
_FONT_FILES = ["msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc", "NotoSansCJK-Regular.ttc"]
_font_cache = {}
_text_cache = {}


def font(size, bold=False):
    key = (size, bold)
    if key not in _font_cache:
        f = None
        windir = os.environ.get("WINDIR", r"C:\Windows")
        for fn in (["msyhbd.ttc"] if bold else []) + _FONT_FILES:
            p = os.path.join(windir, "Fonts", fn)
            if os.path.exists(p):
                try:
                    f = pygame.font.Font(p, size)
                    break
                except (OSError, pygame.error):
                    pass
        if f is None:
            f = pygame.font.SysFont("microsoftyahei,simhei,notosanscjksc,wenquanyimicrohei,arialunicodems", size, bold)
        _font_cache[key] = f
    return _font_cache[key]


def text(s, size, color, bold=False):
    """带缓存的文字渲染（常变的数字也走这里，缓存满了整体清空）。"""
    key = (s, size, color, bold)
    img = _text_cache.get(key)
    if img is None:
        if len(_text_cache) > 3000:
            _text_cache.clear()
        img = font(size, bold).render(s, True, color)
        _text_cache[key] = img
    return img


def text_shadow(surf, s, size, color, pos, bold=False, shadow=(0, 0, 0), center=False):
    img, sh = text(s, size, color, bold), text(s, size, shadow, bold)
    x, y = pos
    if center:
        x -= img.get_width() // 2
    surf.blit(sh, (x + 1, y + 1))
    surf.blit(img, (x, y))
    return img.get_width()


# ------------------------------------------------------------ 天空/视差 ----
TW = 1024                          # 视差贴图宽度（左右无缝循环）
_cache = {}


def _cached(key, make):
    s = _cache.get(key)
    if s is None:
        s = _cache[key] = make()
    return s


def sky(w, h, mode, tint):
    def make():
        st = STYLE[mode]
        top, bot = mix(st["sky_top"], tint, st["tint"]), mix(st["sky_bot"], tint, st["tint"] * 0.7)
        s = pygame.Surface((w, h)).convert()
        for y in range(h):
            pygame.draw.line(s, mix(top, bot, (y / max(1, h - 1)) ** 1.2), (0, y), (w, y))
        if st.get("stars"):
            rr = random.Random(FX_SEED + w * 7 + h)
            for _ in range(int(w * h / 700)):
                x, y = rr.randrange(w), rr.randrange(max(1, int(h * 0.8)))
                b = rr.randint(120, 255)
                s.set_at((x, y), (b, b, min(255, b + 25)))
                if rr.random() < 0.06:
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        s.set_at((min(w - 1, max(0, x + dx)), min(h - 1, max(0, y + dy))), (b // 2, b // 2, b // 2 + 20))
        return s
    return _cached(("sky", w, h, mode, tint), make)


def _periodic(x, terms):
    return sum(a * math.sin(2 * math.pi * n * x / TW + p) for a, n, p in terms)


def _cloud(s, rr, h):
    cx, cy = rr.uniform(0, TW), rr.uniform(0.07, 0.30) * h
    size = h * rr.uniform(0.035, 0.06)
    puffs = [(rr.uniform(-2.2, 2.2) * size, rr.uniform(-0.5, 0.3) * size, size * rr.uniform(0.7, 1.2)) for _ in range(6)]
    for off in (-TW, 0, TW):
        for dx, dy, r in puffs:
            pygame.draw.circle(s, (232, 240, 248), (cx + dx + off, cy + dy + r * 0.25), r)
        for dx, dy, r in puffs:
            pygame.draw.circle(s, (252, 253, 255), (cx + dx + off, cy + dy), r * 0.92)


def far_layer(h, mode):
    """远景：远山 + 云（月球：地球 + 远处山脊），视差系数 0.1。"""
    def make():
        st = STYLE[mode]
        s = pygame.Surface((TW, h)).convert()
        s.fill(KEY)
        rr = random.Random(FX_SEED + 101)
        g = h * 0.80
        if mode == "moon":
            r = max(5, int(h * 0.07))
            cx, cy = int(TW * 0.68), int(h * 0.2)
            pygame.draw.circle(s, (120, 160, 230), (cx, cy), r + max(1, r // 6))
            pygame.draw.circle(s, (50, 110, 205), (cx, cy), r)
            for _ in range(5):
                a, d = rr.uniform(0, 6.28), rr.uniform(0, r * 0.6)
                pygame.draw.circle(s, (70, 160, 90), (cx + math.cos(a) * d, cy + math.sin(a) * d), r * rr.uniform(0.15, 0.35))
            pygame.draw.circle(s, (240, 245, 255), (cx - r * 0.2, cy - r * 0.6), max(1, r * 0.25))
        elif mode == "ice":
            for i in range(7):
                px = i * TW / 7 + rr.uniform(-40, 40)
                ph = h * rr.uniform(0.16, 0.30)
                pw = ph * rr.uniform(1.2, 1.7)
                for off in (-TW, 0, TW):
                    x = px + off
                    pygame.draw.polygon(s, st["hills"][1], [(x - pw, g), (x, g - ph), (x + pw, g)])
                    pygame.draw.polygon(s, (246, 250, 255), [(x - pw * 0.3, g - ph * 0.7), (x, g - ph), (x + pw * 0.3, g - ph * 0.7),
                                                            (x + pw * 0.1, g - ph * 0.62), (x - pw * 0.08, g - ph * 0.72)])
        if mode != "moon":
            for _ in range(5):
                _cloud(s, rr, h)
        back = [(x, g - h * 0.12 - _periodic(x, [(h * 0.05, 2, 0.4), (h * 0.03, 5, 1.3), (h * 0.012, 11, 2.0)]))
                for x in range(0, TW + 1, 8)]
        front = [(x, g - h * 0.05 - _periodic(x, [(h * 0.035, 3, 2.1), (h * 0.02, 7, 0.2), (h * 0.01, 15, 1.0)]))
                 for x in range(0, TW + 1, 8)]
        pygame.draw.polygon(s, st["hills"][0], back + [(TW, h), (0, h)])
        pygame.draw.polygon(s, st["hills"][1], front + [(TW, h), (0, h)])
        s.set_colorkey(KEY, pygame.RLEACCEL)
        return s
    return _cached(("far", h, mode), make)


def near_layer(h, mode):
    """近景：体育场看台 + 观众 + 灯塔 + 广告牌（月球：环形山山脊），视差系数 0.3。"""
    def make():
        st = STYLE[mode]
        s = pygame.Surface((TW, h)).convert()
        s.fill(KEY)
        rr = random.Random(FX_SEED + 202)
        g = int(h * 0.80)
        if mode == "moon":
            pts = [(x, g - h * 0.06 - _periodic(x, [(h * 0.04, 4, 0.7), (h * 0.02, 9, 2.2), (h * 0.008, 23, 0.3)]))
                   for x in range(0, TW + 1, 6)]
            pygame.draw.polygon(s, st["stand"], pts + [(TW, h), (0, h)])
            for _ in range(10):
                cx, w = rr.uniform(0, TW), h * rr.uniform(0.03, 0.08)
                cy = g - h * rr.uniform(0.0, 0.05)
                for off in (-TW, 0, TW):
                    pygame.draw.ellipse(s, st["stand_line"], (cx + off - w, cy - w * 0.25, 2 * w, w * 0.5))
            # 插在山脊上的小旗
            fx = TW * 0.3
            top = g - h * 0.24
            pygame.draw.line(s, (200, 200, 210), (fx, g - h * 0.06), (fx, top), max(1, h // 200))
            pygame.draw.rect(s, (230, 70, 60), (fx, top, h * 0.06, h * 0.035))
            s.set_colorkey(KEY, pygame.RLEACCEL)
            return s
        g = int(h * 0.775)                          # 广告牌底边略高于跑道线
        top = int(h * 0.57)
        sections = [(40, 470), (552, 470)]          # 两段看台，中间露出远山
        ad_h = max(4, int(h * 0.045))
        dot = max(1, int(h * 0.011))
        for x0, w in sections:
            # 灯塔
            for lx in (x0 + 30, x0 + w - 30):
                pygame.draw.line(s, st["stand_line"], (lx, top), (lx, h * 0.16), max(1, h // 160))
                bw, bh = max(8, h * 0.07), max(4, h * 0.035)
                pygame.draw.rect(s, st["stand_line"], (lx - bw / 2, h * 0.16 - bh, bw, bh))
                for j in range(3):
                    pygame.draw.circle(s, (255, 250, 220), (lx - bw / 2 + bw * (j + 0.5) / 3, h * 0.16 - bh / 2), max(1, bh * 0.3))
            body = [(x0, h), (x0, top + 10), (x0 + 16, top), (x0 + w - 16, top), (x0 + w, top + 10), (x0 + w, h)]
            pygame.draw.polygon(s, st["stand"], body)
            tiers = 4
            for t in range(tiers):
                ty = top + 8 + (g - ad_h - top - 8) * t / tiers
                pygame.draw.line(s, st["stand_line"], (x0 + 4, ty), (x0 + w - 4, ty), 1)
                step = dot * 4
                x = x0 + 8 + (t % 2) * dot * 2
                while x < x0 + w - 8:
                    if (x - x0) % 120 > 12:              # 过道
                        col = mix(rr.choice(CONFETTI[:5]), st["stand"], 0.62)
                        pygame.draw.circle(s, col, (x, ty + dot * 2.2), dot)
                    x += step
            # 广告牌
            n_ads = 4
            for j in range(n_ads):
                ax = x0 + w * j / n_ads
                pygame.draw.rect(s, mix(st["ads"][j % len(st["ads"])], (255, 255, 255), 0.35), (ax, g - ad_h, w / n_ads - 2, ad_h))
                if ad_h >= 12:
                    lab = ["QWOP", "AI 运动会", "跑起来!", "Arena"][j]
                    img = text(lab, max(9, int(ad_h * 0.7)), (255, 255, 255), True)
                    s.blit(img, (ax + (w / n_ads - img.get_width()) / 2, g - ad_h + (ad_h - img.get_height()) / 2))
        s.set_colorkey(KEY, pygame.RLEACCEL)
        return s
    return _cached(("near", h, mode), make)


# ------------------------------------------------------------ 地面分块 ----
CW = 1024
XMIN, XMAX = -16.0, physics.GROUND_END + 6.0     # 地面画到物理地面尽头之后一点
_chunks = OrderedDict()


def _terrain_pts(xa, xb, track):
    xs = [xa] + [x for x, _ in physics.TRACKS[track]["terrain"] if xa < x < xb] + [xb]
    return [(x, physics.terrain_height(x, track)) for x in xs]


def ground_chunk(scale, i, yt, yb, mode, track):
    key = (scale, i, yt, yb, mode, track)
    s = _chunks.get(key)
    if s is not None:
        _chunks.move_to_end(key)
        return s
    s = pygame.Surface((CW, int(round((yt - yb) * scale)))).convert()
    s.fill(KEY)
    _paint_ground(s, XMIN + i * CW / scale, scale, yt, mode, track)
    s.set_colorkey(KEY, pygame.RLEACCEL)
    _chunks[key] = s
    while len(_chunks) > 48:
        _chunks.popitem(last=False)
    return s


def _paint_ground(s, x0, scale, yt, mode, track):
    st = STYLE[mode]
    hh = s.get_height()
    xa, xb = x0 - 0.6, x0 + s.get_width() / scale + 0.6
    th = physics.terrain_height
    t = physics.TRACKS[track]

    def P(x, y):
        return (int(round((x - x0) * scale)), int(round((yt - y) * scale)))

    pts = _terrain_pts(xa, xb, track)
    top = [P(x, y) for x, y in pts]
    pygame.draw.polygon(s, st["soil"], top + [(top[-1][0], hh + 2), (top[0][0], hh + 2)])
    # 土层纹理：浅色地层线 + 石子（月球：陨石坑）
    for d in (0.30, 0.62, 0.95):
        line = [P(x, y - d) for x, y in pts]
        pygame.draw.lines(s, mix(st["soil"], st["soil_dark"], 0.45), False, line, max(1, int(scale * 0.012)))
    for c in range(math.floor(xa * 3), math.ceil(xb * 3)):
        rr = random.Random(FX_SEED * 7 + c)
        for _ in range(3):
            x = c / 3 + rr.random() / 3
            depth = GRASS_TH + 0.06 + rr.random() * 1.2
            if mode == "moon" and rr.random() < 0.25:
                w = rr.uniform(0.08, 0.16) * scale
                cx, cy = P(x, th(x, track) - depth)
                pygame.draw.ellipse(s, st["soil_dark"], (cx - w, cy - w * 0.35, 2 * w, w * 0.7))
                pygame.draw.ellipse(s, st["soil_light"], (cx - w, cy - w * 0.35, 2 * w, w * 0.7), 1)
                continue
            w = max(2, rr.uniform(0.015, 0.045) * scale)
            cx, cy = P(x, th(x, track) - depth)
            pygame.draw.ellipse(s, rr.choice((st["soil_dark"], st["soil_light"])), (cx - w, cy - w * 0.5, 2 * w, w))
    # 草皮：每米一条深浅交替的"修剪条纹"
    for m in range(math.floor(xa), math.ceil(xb)):
        seg = _terrain_pts(max(m, xa), min(m + 1, xb), track)
        up = [P(x, y) for x, y in seg]
        dn = [P(x, y - GRASS_TH) for x, y in reversed(seg)]
        pygame.draw.polygon(s, st["grass"][m % 2], up + dn)
    pygame.draw.lines(s, st["grass_edge"], False, [P(x, y - GRASS_TH) for x, y in pts], max(1, int(scale * 0.015)))
    if st.get("sheen"):          # 冰面高光：斜向白色反光条
        for c in range(math.floor(xa * 2), math.ceil(xb * 2)):
            rr = random.Random(FX_SEED * 11 + c)
            x = c / 2 + rr.random() * 0.3
            w = rr.uniform(0.05, 0.14)
            y0 = th(x, track)
            pygame.draw.polygon(s, (250, 253, 255), [P(x, y0 - 0.012), P(x + w, y0 - 0.012),
                                                      P(x + w - 0.04, y0 - GRASS_TH + 0.012), P(x - 0.04, y0 - GRASS_TH + 0.012)])
    if st.get("tuft") and scale >= 70:
        for c in range(math.floor(xa * 8), math.ceil(xb * 8)):
            rr = random.Random(FX_SEED * 13 + c)
            if rr.random() < 0.5:
                continue
            x = c / 8 + rr.random() / 8
            bx, by = P(x, th(x, track))
            hgt = rr.uniform(0.02, 0.04) * scale
            pygame.draw.line(s, st["tuft"], (bx, by), (bx - hgt * 0.3, by - hgt), 1)
            pygame.draw.line(s, st["tuft"], (bx, by), (bx + hgt * 0.35, by - hgt * 0.9), 1)
    # 白色跑道边线
    pygame.draw.lines(s, (250, 250, 250), False, top, max(2, int(scale * 0.018)))
    pit = t.get("pit")
    if pit and xa <= pit[1] and xb >= pit[0]:      # 跳远沙坑：沙面 + 沙粒（覆盖草皮）
        p0, p1 = max(pit[0], xa), min(pit[1], xb)
        sand, sand_d = (232, 204, 142), (205, 172, 104)
        pygame.draw.polygon(s, sand, [P(p0, 0.012), P(p1, 0.012), P(p1, -0.32), P(p0, -0.32)])
        pygame.draw.line(s, sand_d, P(p0, 0.012), P(p1, 0.012), max(1, int(scale * 0.012)))
        for c in range(math.floor(p0 * 12), math.ceil(p1 * 12)):
            rr = random.Random(FX_SEED * 17 + c)
            x = c / 12 + rr.random() / 12
            if not (p0 < x < p1):
                continue
            cx, cy = P(x, -rr.random() * 0.3)
            pygame.draw.circle(s, rr.choice((sand_d, (244, 222, 170))), (cx, cy), max(1, int(scale * 0.008)))
        if xa <= pit[0] <= xb:
            pygame.draw.line(s, (150, 120, 70), P(pit[0], 0.0), P(pit[0], -0.32), 2)
    line = t.get("line")
    # 每米刻度 + 标签（土里）；跳远沙坑内改标"距起跳线"的米数
    fs = max(10, min(17, int(scale * 0.12)))
    for m in range(max(-3, math.ceil(xa)), math.floor(xb) + 1):
        y = th(m, track)
        a, b = P(m, y), P(m, y - GRASS_TH)
        if line is not None and m > line:
            if m - line <= 1e-6 or (pit and m >= pit[1]):
                continue
            a2 = P(m, y - 0.06)
            pygame.draw.line(s, (150, 118, 66), a, a2, 2)
            text_shadow(s, f"{m - line:g}m", fs, (120, 84, 30), (a2[0], a2[1] + 2), bold=True, shadow=(250, 236, 200), center=True)
            continue
        pygame.draw.line(s, (250, 250, 250), a, b, 3 if m % 5 == 0 else 2)
        lab = f"{m}m"
        text_shadow(s, lab, fs, st["label"], (b[0], b[1] + 3), bold=(m % 5 == 0), shadow=st["label_shadow"], center=True)
    if line is not None and xa - 1 <= line <= xb + 1:   # 起跳板（线后 0.2m 白板）+ 红色起跳线
        a, b = P(line - 0.2, 0.012), P(line, -GRASS_TH)
        pygame.draw.rect(s, (250, 250, 250), (a[0], a[1], b[0] - a[0], b[1] - a[1]))
        pygame.draw.rect(s, (150, 150, 150), (a[0], a[1], b[0] - a[0], b[1] - a[1]), 1)
        a, b = P(line - 0.015, 0.02), P(line + 0.015, -0.32)
        pygame.draw.rect(s, (220, 40, 40), (a[0], a[1], max(3, b[0] - a[0]), b[1] - a[1]))
        text_shadow(s, "起跳线", fs, (200, 30, 30), (P(line, 0)[0], P(line, -0.34)[1]), bold=True, shadow=(255, 255, 255), center=True)
    wall = t.get("end_wall")
    if wall is not None and xa - 1 <= wall <= xb + 1:
        a, b = P(wall - 0.07, 4.0), P(wall + 0.07, 0)
        pygame.draw.rect(s, (150, 152, 160), (a[0], a[1], b[0] - a[0], b[1] - a[1]))
    # 起跑线
    if xa <= 0 <= xb:
        pygame.draw.polygon(s, (255, 255, 255), [P(-0.03, 0), P(0.03, 0), P(0.03, -GRASS_TH), P(-0.03, -GRASS_TH)])
    # 起点挡墙
    if xa <= -3 <= xb:
        a, b = P(-3.07, 4.0), P(-2.93, 0)
        pygame.draw.rect(s, (150, 152, 160), (a[0], a[1], b[0] - a[0], b[1] - a[1]))
        pygame.draw.rect(s, (110, 112, 120), (a[0], a[1], b[0] - a[0], b[1] - a[1]), 2)
        for j in range(6):           # 底部警示条
            yy = P(0, 0.06 * j)[1]
            pygame.draw.rect(s, (240, 200, 40) if j % 2 else (40, 40, 40), (a[0], yy - 0.06 * scale, b[0] - a[0], 0.06 * scale))
    # 减速带
    for bx, bh, bw in t["bumps"]:
        if xa - 1 <= bx <= xb + 1:
            a, b = P(bx - bw / 2, bh), P(bx + bw / 2, 0)
            r = pygame.Rect(a[0], a[1], b[0] - a[0], b[1] - a[1] + 1)
            pygame.draw.rect(s, (245, 205, 40), r, border_radius=max(1, r.h // 3))
            s.set_clip(r)
            sw = max(3, r.w // 5)
            for xx in range(r.x - r.h, r.right + r.h, sw * 2):
                pygame.draw.polygon(s, (40, 40, 40), [(xx, r.bottom), (xx + sw, r.bottom), (xx + sw + r.h, r.y), (xx + r.h, r.y)])
            s.set_clip(None)
            pygame.draw.rect(s, (60, 50, 20), r, 1, border_radius=max(1, r.h // 3))
    # 区域路牌
    sfs = max(10, min(18, int(scale * 0.13)))
    for zx, zname in t["zones"]:
        if ("line" not in t and zx >= physics.TRACK_LENGTH) or not (xa - 1.5 <= zx <= xb + 1.5):
            continue
        sx = zx + 0.4                        # 路牌插在区域起点稍前方，开跑时不挡在小人身后
        g = th(sx, track)
        base, tip = P(sx, g), P(sx, g + 0.42)
        pygame.draw.line(s, (96, 96, 108), base, tip, max(2, int(scale * 0.025)))
        img = text(zname, sfs, INK, True)
        bw, bh = img.get_width() + 12, img.get_height() + 4
        board = pygame.Rect(tip[0] - bw // 2, tip[1] - bh, bw, bh)
        col = ZONE_COLORS.get(zname, (120, 120, 120))
        pygame.draw.rect(s, (255, 255, 255), board, border_radius=4)
        pygame.draw.rect(s, col, (board.x, board.y, 5, board.h), border_top_left_radius=4, border_bottom_left_radius=4)
        pygame.draw.rect(s, col, board, 2, border_radius=4)
        s.blit(img, (board.x + 8, board.y + 2))
    # 终点：地面黑白格 + 拱门横幅
    fx = physics.TRACK_LENGTH
    if "line" not in t and xa - 1 <= fx <= xb + 1:
        g = th(fx, track)
        cells = 6
        for j in range(cells):
            for row in range(2):
                xx = fx - 0.12 + 0.24 * j / cells
                c = (20, 20, 20) if (j + row) % 2 else (250, 250, 250)
                pygame.draw.polygon(s, c, [P(xx, g - GRASS_TH * row / 2), P(xx + 0.24 / cells, g - GRASS_TH * row / 2),
                                           P(xx + 0.24 / cells, g - GRASS_TH * (row + 1) / 2), P(xx, g - GRASS_TH * (row + 1) / 2)])
        pw = max(3, int(scale * 0.05))
        for px in (fx - 0.5, fx + 0.5):
            a, b = P(px, g + 1.78), P(px, g)
            pygame.draw.rect(s, (235, 235, 240), (a[0] - pw // 2, a[1], pw, b[1] - a[1]))
            pygame.draw.rect(s, (120, 120, 130), (a[0] - pw // 2, a[1], pw, b[1] - a[1]), 1)
        a, b = P(fx - 0.62, g + 1.92), P(fx + 0.62, g + 1.64)
        ban = pygame.Rect(a[0], a[1], b[0] - a[0], b[1] - a[1])
        n = max(4, ban.w // max(4, ban.h // 2))
        cw, ch = ban.w / n, ban.h / 2
        for j in range(n):
            for row in range(2):
                c = (20, 20, 20) if (j + row) % 2 else (250, 250, 250)
                pygame.draw.rect(s, c, (ban.x + j * cw, ban.y + row * ch, math.ceil(cw), math.ceil(ch)))
        pygame.draw.rect(s, INK, ban, 2)
        img = text("终点", max(11, min(28, int(ban.h * 0.8))), (200, 30, 30), True)
        plate = pygame.Rect(0, 0, img.get_width() + 10, ban.h + 6)
        plate.center = ban.center
        pygame.draw.rect(s, (255, 236, 120), plate, border_radius=4)
        pygame.draw.rect(s, (200, 30, 30), plate, 2, border_radius=4)
        s.blit(img, (plate.centerx - img.get_width() // 2, plate.centery - img.get_height() // 2))
        for px in (fx - 0.62, fx + 0.62):        # 三角小旗
            tx, ty = P(px, g + 1.92)
            fl = max(5, int(scale * 0.12))
            pygame.draw.line(s, (90, 90, 100), (tx, ty), (tx, ty - fl), 2)
            pygame.draw.polygon(s, (230, 60, 60), [(tx, ty - fl), (tx + fl * 0.8, ty - fl * 0.8), (tx, ty - fl * 0.6)])


# ------------------------------------------------------------ 精灵 --------
def shadow_sprite(w, h, alpha):
    def make():
        s = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.ellipse(s, (0, 0, 0, alpha // 2), s.get_rect())
        pygame.draw.ellipse(s, (0, 0, 0, alpha), s.get_rect().inflate(-w // 3, -h // 3))
        return s
    return _cached(("shadow", w, h, alpha), make)


def puff_sprite(r, alpha, color):
    def make():
        s = pygame.Surface((2 * r + 2, 2 * r + 2), pygame.SRCALPHA)
        pygame.draw.circle(s, (*shade(color, 0.82), alpha // 2), (r + 1, r + 1), r)
        pygame.draw.circle(s, (*color, alpha), (r + 1 - r // 5, r + 1 - r // 5), max(1, int(r * 0.7)))
        return s
    return _cached(("puff", r, alpha, color), make)


def panel(w, h, color, alpha, radius=8):
    def make():
        s = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(s, (*color, alpha), s.get_rect(), border_radius=radius)
        return s
    return _cached(("panel", w, h, color, alpha, radius), make)


def draw_hurdle(surf, pts, outline):
    """栏架：沿长边红白相间的条纹。pts 为盒子 4 个角（局部 -x-y, +x-y, +x+y, -x+y）。"""
    (ax, ay), (bx, by), (cx, cy), (dx, dy) = pts
    n = 4
    for j in range(n):
        t0, t1 = j / n, (j + 1) / n
        q = [(ax + (dx - ax) * t0, ay + (dy - ay) * t0), (bx + (cx - bx) * t0, by + (cy - by) * t0),
             (bx + (cx - bx) * t1, by + (cy - by) * t1), (ax + (dx - ax) * t1, ay + (dy - ay) * t1)]
        pygame.draw.polygon(surf, (225, 45, 45) if j % 2 == 0 else (250, 250, 250), q)
    pygame.draw.polygon(surf, outline, pts, 1)


# ------------------------------------------------------------ 小图标 -------
ICON_OF = {"walk": "walk", "back": "fall", "face": "fall", "faceslide": "fall", "split": "fall", "unbalanced": "fall",
           "crawl": "crawl", "struggle": "crawl", "fly": "fly"}


def draw_icon(s, kind, cx, cy, sz, col):
    """手绘矢量小图标（不依赖 emoji 字体）。sz 为图标边长。"""
    u = sz / 16.0
    w = max(1, int(round(1.6 * u)))
    if kind == "walk":            # 奔跑小人
        pygame.draw.circle(s, col, (cx + 2 * u, cy - 5.5 * u), 2.2 * u)
        for a, b in (((1.5, -3), (-0.5, 2)), ((-0.5, 2), (3, 5)), ((3, 5), (2.5, 8)), ((-0.5, 2), (-3.5, 5)),
                     ((-3.5, 5), (-6, 4.5)), ((1, -1.5), (4.5, 0.5)), ((1, -1.5), (-2.5, -0.5)), ((-2.5, -0.5), (-4, 1.5))):
            pygame.draw.line(s, col, (cx + a[0] * u, cy + a[1] * u), (cx + b[0] * u, cy + b[1] * u), w)
    elif kind == "fall":          # 撞击爆炸星
        pts = []
        for j in range(16):
            r = (7.5 if j % 2 == 0 else 3.5) * u
            a = j * math.pi / 8 + 0.2
            pts.append((cx + math.cos(a) * r, cy + math.sin(a) * r))
        pygame.draw.polygon(s, (250, 190, 40), pts)
        pygame.draw.polygon(s, (220, 60, 40), pts, max(1, w // 2 + 1))
    elif kind == "crawl":         # 毛毛虫
        for j in range(4):
            x = cx - 5.5 * u + j * 3.4 * u
            y = cy + 2 * u - (1.5 * u if j in (1, 2) else 0)
            pygame.draw.circle(s, (90, 180, 80), (x, y), 2.6 * u)
            pygame.draw.circle(s, (40, 110, 40), (x, y), 2.6 * u, 1)
        pygame.draw.circle(s, (30, 30, 30), (cx + 5.3 * u, cy + 1.2 * u), max(1, 0.8 * u))
    elif kind == "fly":           # 火箭
        body = [(cx + 7 * u, cy - 7 * u), (cx + 3 * u, cy + 3 * u), (cx - 3 * u, cy - 3 * u)]
        pygame.draw.polygon(s, (230, 235, 245), body)
        pygame.draw.polygon(s, (90, 100, 120), body, 1)
        pygame.draw.circle(s, (70, 150, 230), (cx + 3.2 * u, cy - 3.2 * u), 1.6 * u)
        pygame.draw.polygon(s, (250, 140, 40), [(cx - 1.5 * u, cy + 1.5 * u), (cx - 7 * u, cy + 7 * u), (cx - 3.8 * u, cy + 0.5 * u),
                                                (cx - 0.5 * u, cy - 1.2 * u)])


ZONE_CHEER = {"减速带": "减速带来了", "上坡": "冲上坡！", "下坡": "下坡啦", "跨栏": "跨栏区！"}
BUBBLE_EXTRA = {"getup": ("爬起来了！", "walk")}       # 不在 evaluator.LABELS 里的气泡：key → (文字, 图标)


def bubble_label(key):
    """气泡 key → (文字, 图标)：姿态标签、"getup"、"zone:<区名>"。"""
    if key in BUBBLE_EXTRA:
        return BUBBLE_EXTRA[key]
    if key.startswith("zone:"):
        zn = key[5:]
        return ZONE_CHEER.get(zn, f"进入{zn}"), "walk"
    name = evaluator.LABELS[key][0]
    return (name.split(" ", 1)[1] if " " in name else name), ICON_OF.get(key)


def bubble_sprite(label, fs, color, tail_dir="down"):
    """语音气泡：圆角框 + 小尾巴 + 可选图标 + 中文文字。tail_dir="down" 尾巴尖在底部中间，
    "left" 尾巴尖在左侧中间（头顶放不下时把气泡挂在头的右边，不挡脸）。"""
    def make():
        name, icon = bubble_label(label)
        img = text(name, fs, INK, True)
        isz = int(fs * 1.15) if icon else 0
        pad = max(5, fs // 3)
        bw = img.get_width() + 2 * pad + (isz + 4 if icon else 0)
        bh = max(img.get_height(), isz) + pad
        tail = max(5, fs // 2)
        if tail_dir == "left":
            s = pygame.Surface((bw + tail + 4, bh + 4), pygame.SRCALPHA)
            r = pygame.Rect(tail + 2, 2, bw, bh)
            my = r.centery
            pygame.draw.polygon(s, color, [(r.x + 3, my - tail * 0.8), (r.x + 3, my + tail * 0.8), (r.x - tail - 1, my + 1)])
            pygame.draw.rect(s, color, r.inflate(4, 4), border_radius=bh // 2 + 2)
            pygame.draw.rect(s, (255, 255, 255), r, border_radius=bh // 2)
            pygame.draw.polygon(s, (255, 255, 255), [(r.x + 4, my - tail * 0.8 + 3), (r.x + 4, my + tail * 0.8 - 3), (r.x - tail + 3, my + 1)])
        elif tail_dir == "right":
            s = pygame.Surface((bw + tail + 4, bh + 4), pygame.SRCALPHA)
            r = pygame.Rect(2, 2, bw, bh)
            my, rx = r.centery, r.right
            pygame.draw.polygon(s, color, [(rx - 3, my - tail * 0.8), (rx - 3, my + tail * 0.8), (rx + tail + 1, my + 1)])
            pygame.draw.rect(s, color, r.inflate(4, 4), border_radius=bh // 2 + 2)
            pygame.draw.rect(s, (255, 255, 255), r, border_radius=bh // 2)
            pygame.draw.polygon(s, (255, 255, 255), [(rx - 4, my - tail * 0.8 + 3), (rx - 4, my + tail * 0.8 - 3), (rx + tail - 3, my + 1)])
        else:
            s = pygame.Surface((bw + 4, bh + tail + 4), pygame.SRCALPHA)
            r = pygame.Rect(2, 2, bw, bh)
            mx = r.centerx
            pygame.draw.polygon(s, color, [(mx - tail, r.bottom - 3), (mx + tail, r.bottom - 3), (mx - 1, r.bottom + tail + 1)])
            pygame.draw.rect(s, color, r.inflate(4, 4), border_radius=bh // 2 + 2)
            pygame.draw.rect(s, (255, 255, 255), r, border_radius=bh // 2)
            pygame.draw.polygon(s, (255, 255, 255), [(mx - tail + 3, r.bottom - 4), (mx + tail - 3, r.bottom - 4), (mx - 1, r.bottom + tail - 2)])
        x = r.x + pad
        if icon:
            draw_icon(s, icon, x + isz // 2, r.centery, isz, shade(color, 0.8))
            x += isz + 4
        s.blit(img, (x, r.centery - img.get_height() // 2))
        return s
    return _cached(("bubble", label, fs, color, tail_dir), make)


# ------------------------------------------------------------ 脸 ----------
def draw_face(surf, c, R, ang, expr, k, ink=INK):
    """c: 头心屏幕坐标；R: 头半径(px)；ang: 躯干 pymunk 角度（y 向上，逆时针为正）。"""
    ca, sa = math.cos(ang), math.sin(ang)

    def L(lx, ly):           # 头部局部坐标（以 R 为单位，+x 朝前）→ 屏幕
        return (c[0] + (lx * ca - ly * sa) * R, c[1] - (lx * sa + ly * ca) * R)

    if R < 3.5:
        pygame.draw.circle(surf, ink, L(0.45, 0.2), 1)
        return
    lw = max(1, int(R * 0.14))
    eyes = (L(0.12, 0.22), L(0.55, 0.22))
    if expr == "dizzy":
        for j, (ex, ey) in enumerate(eyes):
            pts = []
            for q in range(14):
                tt = q / 13 * 4 * math.pi
                rr = R * (0.05 + 0.2 * q / 13)
                a = tt + k * 0.25 * (1 if j else -1)
                pts.append((ex + math.cos(a) * rr, ey + math.sin(a) * rr))
            pygame.draw.lines(surf, ink, False, pts, 1)
        pygame.draw.lines(surf, ink, False, [L(0.12 + 0.12 * q, -0.42 + (0.07 if q % 2 else -0.04)) for q in range(5)], lw)
        if R >= 5:                   # 头顶转圈的小星星
            for j in range(3):
                a = k * 0.12 + j * 2.094
                sx, sy = c[0] + math.cos(a) * R * 1.5, c[1] - R * 1.25 + math.sin(a) * R * 0.45
                rs = max(2, R * 0.32)
                star =[(sx + math.cos(i * math.pi / 5 - 1.57) * (rs if i % 2 == 0 else rs * 0.45),
                         sy + math.sin(i * math.pi / 5 - 1.57) * (rs if i % 2 == 0 else rs * 0.45)) for i in range(10)]
                pygame.draw.polygon(surf, (255, 215, 40), star)
    elif expr == "grin":
        for ex, ey in eyes:
            pygame.draw.lines(surf, ink, False, [(ex - R * 0.16, ey + R * 0.06), (ex, ey - R * 0.12), (ex + R * 0.16, ey + R * 0.06)], lw)
        mouth = [L(0.42 + 0.36 * math.cos(a), -0.2 + 0.36 * math.sin(a)) for a in [math.pi + q * math.pi / 8 for q in range(9)]]
        pygame.draw.polygon(surf, (130, 30, 40), mouth)
        pygame.draw.line(surf, (255, 255, 255), mouth[0], mouth[-1], max(1, lw))
    elif expr == "surprised":
        for e in eyes:
            pygame.draw.circle(surf, (255, 255, 255), e, max(2, R * 0.2))
            pygame.draw.circle(surf, ink, e, max(1, R * 0.11))
        pygame.draw.circle(surf, (130, 30, 40), L(0.45, -0.42), max(2, R * 0.17))
    else:
        for e in eyes:
            pygame.draw.circle(surf, ink, e, max(1, R * 0.12))
        pygame.draw.lines(surf, ink, False, [L(0.2, -0.32), L(0.42, -0.46), L(0.68, -0.34)], lw)


# ------------------------------------------------------------ 特效预计算 --
SHAKE_LIFE = 36
DUST_LIFE = 60
DEBRIS_LIFE = 70
CONFETTI_LIFE = 240
FALL_GAP = 60          # 躯干此前至少 1 秒没触地，才算一次"摔倒"


def _local(pose_b, lx, ly):
    px, py, a = pose_b
    ca, sa = math.cos(a), math.sin(a)
    return (px + lx * ca - ly * sa) / PPM, (py + lx * sa + ly * ca) / PPM


class LaneFX:
    """一个选手的全部装饰性事件，载入时由录制帧一次性算好（rng 由调用方按固定顺序传入）。"""

    def __init__(self, frames, poses, down, labels, finish_k, rng):
        n = len(frames)
        self.dust, self.debris, self.shakes = [], [], []      # 按帧号有序
        self.falls, self.hurdle_hits = [], []
        self.finish_k = finish_k
        # 脚落地扬尘（脚掌是独立刚体：扬尘锚在脚掌底边中点）
        foot_ly = -physics.FOOT_H / 2
        for foot, key in ((FOOT_IDX[0], "foot_L"), (FOOT_IDX[1], "foot_R")):
            last = -99
            for k in range(2, n):
                c = frames[k]["contact"]
                if c[key] and not frames[k - 1]["contact"][key] and k - last > 12:
                    vy = -(poses[k - 1][foot][1] - poses[k - 2][foot][1]) * FPS / PPM
                    if vy > 2.5:
                        x, y = _local(poses[k][foot], 0.0, foot_ly)
                        self._add_dust(k, x, y, min(1.0, (vy - 2.5) / 4.0) * 0.6 + 0.15, rng)
                        last = k
        # 躯干/头砸地：扬尘 + 镜头震动；久未触地后的第一次触地记为"摔倒"
        last_touch, last_small = -10 ** 6, -99
        head_ly = physics.TORSO_H / 2 + physics.HEAD_R
        corners = [(sx * physics.TORSO_W / 2, sy * physics.TORSO_H / 2) for sx in (-1, 1) for sy in (-1, 1)]
        for k in range(1, n):
            if frames[k]["contact"]["torso"] and not frames[k - 1]["contact"]["torso"]:
                speed = max(0.0, -frames[k - 1]["vy"])
                cand = [_local(poses[k][0], lx, ly) for lx, ly in corners]
                hx, hy = _local(poses[k][0], 0, head_ly)
                cand.append((hx, hy - physics.HEAD_R / PPM))
                x, y = min(cand, key=lambda p: p[1])
                if k - last_touch > FALL_GAP and k > 20:
                    self.falls.append(k)
                    self._add_dust(k, x, y, 1.0, rng)
                    self.shakes.append((k, 6.0 + 5.0 * min(1.0, speed / 3.0), rng.uniform(0, 6.28)))
                elif speed > 0.8 and k - last_small > 20:
                    self._add_dust(k, x, y, 0.5, rng)
                    self.shakes.append((k, 2.5 * min(1.0, speed / 2.0) + 1.0, rng.uniform(0, 6.28)))
                    last_small = k
            if frames[k]["contact"]["torso"]:
                last_touch = k
        # 栏架被碰：碎片 + 火花
        for h in range(HURDLE0, len(poses[0])):
            last = -99
            for k in range(1, n):
                (x0, y0, a0), (x1, y1, a1) = poses[k - 1][h], poses[k][h]
                v = math.hypot(x1 - x0, y1 - y0) * FPS / PPM + abs(a1 - a0) * FPS * 0.1
                if v > 0.6 and k - last > 40:
                    if last < 0:
                        self.hurdle_hits.append(k)
                    last = k
                    self._add_debris(k, x1 / PPM, y1 / PPM, 1 if x1 >= x0 else -1, rng)
        # 冲线彩纸
        self.confetti = None
        if finish_k is not None:
            parts = [(rng.uniform(-2.8, 2.2), rng.uniform(0.1, 1.6), rng.randrange(len(CONFETTI)),
                      rng.uniform(0.03, 0.06), rng.uniform(0, 6.28), rng.uniform(4, 10)) for _ in range(90)]
            self.confetti = (finish_k, physics.TRACK_LENGTH, physics.terrain_height(physics.TRACK_LENGTH) + 1.85, parts)
        self.dust.sort(key=lambda e: e[0])
        self.debris.sort(key=lambda e: e[0])
        self._dust_k = [e[0] for e in self.dust]
        self._debris_k = [e[0] for e in self.debris]
        self._shake_k = [e[0] for e in self.shakes]
        # 倒地段：[(k0, k1, x_before, loss)]，loss = 该段被扣掉的水平位移
        self.down_runs = []
        k = 0
        while k < n:
            if down[k]:
                k0 = k
                while k + 1 < n and down[k + 1]:
                    k += 1
                xb = frames[k0 - 1]["x"] if k0 > 0 else 0.0
                self.down_runs.append((k0, k, xb, frames[k]["x"] - xb))
            k += 1
        self._down_k = [r[0] for r in self.down_runs]
        # 气泡：去抖动后的姿态（新姿态持续 12 帧才切换），并记下切换帧做弹出动画
        self.bubble, self.bubble_since = [], []
        cur, since, cand, run = labels[0], 0, None, 0
        for k, lab in enumerate(labels):
            if lab == cur:
                cand, run = None, 0
            elif lab == cand:
                run += 1
                if run >= 12:
                    cur, since, cand, run = lab, k, None, 0
            else:
                cand, run = lab, 1
            self.bubble.append(cur)
            self.bubble_since.append(since)

    def _add_dust(self, k, x, y, strength, rng):
        parts = []
        for _ in range(4 + int(8 * strength)):
            side = rng.choice((-1, 1))
            parts.append((side * rng.uniform(0.2, 1.0) * (0.4 + strength), rng.uniform(0.05, 0.5) * (0.4 + strength),
                          rng.uniform(0.04, 0.08) * (0.8 + strength), rng.uniform(0.5, 1.0)))
        self.dust.append((k, x, y, parts))

    def _add_debris(self, k, x, y, direction, rng):
        chips = [(direction * rng.uniform(0.3, 2.0), rng.uniform(0.6, 2.4), rng.uniform(0.02, 0.045),
                  rng.uniform(-12, 12), rng.randrange(2), rng.uniform(0.7, 1.15)) for _ in range(8)]
        sparks = [(math.cos(a) * sp, math.sin(a) * sp) for a, sp in
                  ((rng.uniform(0.15, math.pi - 0.15), rng.uniform(1.5, 3.5)) for _ in range(9))]
        self.debris.append((k, x, y, chips, sparks))

    # -- 查询 --
    @staticmethod
    def _active(keys, events, k, life):
        lo = bisect.bisect_left(keys, k - life + 1)
        hi = bisect.bisect_right(keys, k)
        return events[lo:hi]

    def shake(self, k, scale):
        ox = oy = 0.0
        for k0, amp, ph in self._active(self._shake_k, self.shakes, k, SHAKE_LIFE):
            t = (k - k0) / FPS
            a = amp * math.exp(-t / 0.09) * scale / 100.0
            ox += a * math.sin(2 * math.pi * 23 * t + ph)
            oy += a * math.cos(2 * math.pi * 17 * t + ph * 1.7)
        return ox, oy

    def runs_until(self, k):
        """k 之前（含）开始的所有倒地段。"""
        return self.down_runs[:bisect.bisect_right(self._down_k, k)]

    def draw_dust(self, surf, view, k, color):
        for k0, x, y, parts in self._active(self._dust_k, self.dust, k, DUST_LIFE):
            t = (k - k0) / FPS
            e = (1 - math.exp(-3 * t)) / 3
            for vx, vy, r0, life in parts:
                if t >= life:
                    continue
                u = t / life
                sx, sy = view.w2s(x + vx * e, y + vy * e + 0.05 * t)
                r = max(1, int(r0 * (1 + 1.6 * u) * view.scale))
                a = int(230 * (1 - u) ** 1.3) // 16 * 16
                if a > 0:
                    img = puff_sprite(min(r, 60), a, color)
                    surf.blit(img, (sx - img.get_width() // 2, sy - img.get_height() // 2))

    def draw_debris(self, surf, view, k, ground_y):
        for k0, x, y, chips, sparks in self._active(self._debris_k, self.debris, k, DEBRIS_LIFE):
            t = (k - k0) / FPS
            if t < 0.35:
                u = t / 0.35
                for vx, vy in sparks:
                    p = (x + vx * t, y + vy * t - 2 * t * t)
                    q = (p[0] - vx * 0.035, p[1] - vy * 0.035)
                    pygame.draw.line(surf, mix((255, 250, 200), (255, 150, 30), u), view.w2s(*p), view.w2s(*q), 2)
            for vx, vy, size, spin, col, life in chips:
                if t >= life:
                    continue
                px = x + vx * t
                py = max(ground_y(px), y + vy * t - 4.5 * t * t)
                a = spin * t
                hs = size / 2
                pts = [view.w2s(px + math.cos(a + q) * hs * 1.4, py + math.sin(a + q) * hs * 1.4)
                       for q in (0.785, 2.356, 3.927, 5.498)]
                pygame.draw.polygon(surf, (225, 45, 45) if col else (250, 250, 250), pts)

    def draw_confetti(self, surf, view, k):
        if self.confetti is None:
            return
        k0, x, y, parts = self.confetti
        if not (k0 <= k < k0 + CONFETTI_LIFE):
            return
        t = (k - k0) / FPS
        e = (1 - math.exp(-1.6 * t)) / 1.6
        for vx, vy, ci, size, ph, spin in parts:
            px = x + vx * e + 0.12 * math.sin(3 * t + ph)
            py = y + vy * e - 0.75 * t
            flip = math.cos(spin * t + ph)
            a = ph + t * spin * 0.3
            w, h = size * max(0.15, abs(flip)), size * 0.6
            ca, sa = math.cos(a), math.sin(a)
            pts = [view.w2s(px + dx * ca - dy * sa, py + dx * sa + dy * ca) for dx, dy in ((-w, -h), (w, -h), (w, h), (-w, h))]
            pygame.draw.polygon(surf, CONFETTI[ci] if flip > 0 else shade(CONFETTI[ci], 0.75), pts)


# ------------------------------------------------------------ 气泡排期 ----
# 气泡不再一直挂在头顶：只在值得一提的时刻冒一下（摔倒、极端姿态、爬起来、第一次进入新赛段、
# 持续 1 秒以上的怪姿势），每条赛道两个气泡之间至少隔 BUBBLE_GAP 秒（摔倒/起飞/一字马除外），
# 同一句话 BUBBLE_REPEAT 秒内不重复，全场同一时刻最多 1 个普通气泡（摔倒类可以再叠 1 个）。
# 纯函数：只依赖录制数据，不用随机数 → 完全确定。时间单位是回放帧（慢动作窗口里屏幕停留会更久）。
BUBBLE_MODES = ("few", "normal", "off")
BUBBLE_GAP = 3.5
BUBBLE_REPEAT = 6.0
BUBBLE_DUR = {3: 2.0, 2: 1.6, 1: 1.4}       # 按优先级的显示秒数
DOWNISH = ("back", "face", "faceslide", "split", "struggle", "crawl")
FALLISH = ("back", "face", "faceslide", "split")
ODD_POSTURES = ("sitback", "hunch", "crawl", "retreat")


def _segments(seq):
    """去抖后的逐帧标签 → [(起始帧, 结束帧(含), 标签)]。"""
    out, k0 = [], 0
    for k in range(1, len(seq) + 1):
        if k == len(seq) or seq[k] != seq[k0]:
            out.append((k0, k - 1, seq[k0]))
            k0 = k
    return out


def _bubble_candidates(lfx, frames, down, track):
    """一条赛道的候选气泡 [(k, 优先级, key)]。"""
    n = len(frames)
    seq = lfx.bubble
    cand = []
    for k in lfx.falls:                          # 摔倒：取随后 0.5 秒里第一个倒地类姿态做文字
        lab = next((seq[j] for j in range(k, min(n, k + 30)) if seq[j] in FALLISH), None)
        lab = lab or next((seq[j] for j in range(k, min(n, k + 30)) if seq[j] in DOWNISH), "unbalanced")
        cand.append((k, 3, lab))
    for k0, k1, lab in _segments(seq):
        if k0 == 0:
            continue
        if lab in ("fly", "split"):              # 极端姿态
            cand.append((k0, 3, lab))
        elif lab in ODD_POSTURES and k1 - k0 >= FPS:     # 持续 1 秒以上的怪姿势
            cand.append((k0, 1, lab))
    for k0, k1, _xb, _loss in lfx.down_runs:     # 倒地 ≥1 秒后重新站起来并保持 0.5 秒
        e = k1 + 1
        if k0 > 0 and k1 - k0 >= FPS and e + 30 < n and not any(down[e:e + 30]):
            cand.append((e + 15, 2, "getup"))
    zones = physics.TRACKS[track]["zones"]
    for za, zn in zones:                         # 第一次进入新赛段
        if za <= 0 or zn == "终点":
            continue
        k = next((j for j in range(n) if frames[j]["x"] >= za), None)
        if k is not None:
            cand.append((k, 1, "zone:" + zn))
    return cand


def schedule_bubbles(lane_fx, lane_frames, lane_down, track, mode="few"):
    """给每个 LaneFX 填 .bubble_events = [(k0, k1, key)] 和逐帧索引 .bubble_at（-1 = 不显示）。"""
    n = len(lane_frames[0]) if lane_frames else 0
    if mode == "normal":                          # 旧行为：去抖后的姿态一直挂在头顶
        for lfx in lane_fx:
            lfx.bubble_events = [(a, b + 1, lab) for a, b, lab in _segments(lfx.bubble)]
    elif mode == "off":
        for lfx in lane_fx:
            lfx.bubble_events = []
    else:
        allc = []
        for i, (lfx, fr, dn) in enumerate(zip(lane_fx, lane_frames, lane_down)):
            allc += [(k, -p, i, key) for k, p, key in _bubble_candidates(lfx, fr, dn, track) if k >= 30]
        allc.sort()
        ev = [[] for _ in lane_fx]               # 每条赛道 [k0, k1, key, prio]（k1 可能被截短）
        gap, rep = int(BUBBLE_GAP * FPS), int(BUBBLE_REPEAT * FPS)
        for k, negp, i, key in allc:
            p = -negp
            mine = ev[i]
            if any(e[2] == key and k - e[0] < rep for e in mine):
                continue
            visible = [(j, e) for j, es in enumerate(ev) for e in es if e[0] <= k < e[1]]
            if p >= 3:
                others = [(j, e) for j, e in visible if j != i]
                if len(others) >= 2:
                    low = [e for _j, e in others if e[3] < 3]
                    if not low:
                        continue
                    low[0][1] = k
                for e in mine:                   # 打断自己赛道正在显示的气泡
                    if e[1] > k:
                        e[1] = k
            else:
                if mine and (k - mine[-1][0] < gap or k < mine[-1][1]):
                    continue
                if visible:                      # 别的赛道正在冒泡：最多顺延 1 秒等它收起
                    k2 = max(e[1] for _j, e in visible)
                    if k2 - k > FPS or any(e[0] <= k2 < e[1] or k <= e[0] < k2 for es in ev for e in es if e not in
                                           [ve for _j, ve in visible]):
                        continue
                    k = k2 + 6
                    if k >= n or (mine and k - mine[-1][0] < gap):
                        continue
            mine.append([k, min(n, k + int(BUBBLE_DUR[p] * FPS)), key, p])
        for lfx, es in zip(lane_fx, ev):
            lfx.bubble_events = [(a, b, key) for a, b, key, _p in es if b > a]
    for lfx in lane_fx:
        at = [-1] * n
        for j, (a, b, _key) in enumerate(lfx.bubble_events):
            for k in range(a, min(b, n)):
                at[k] = j
        lfx.bubble_at = at
        lfx.bubble_mode = mode
