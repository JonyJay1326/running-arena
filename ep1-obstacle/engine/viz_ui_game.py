"""--ui game：回放画面的"游戏风格"界面（2026-10-29，纯渲染，不碰模拟/计分/随机数）。

visualizer.py 在 UI_STYLE == "game" 时把顶栏、选手卡、头顶气泡/扣分、冲线横幅、成绩榜、片头、悬停/帮助/toast
转到这里画；不加 --ui game 时本文件一行都不会被调用，画面与改版前逐字节一致。

视觉语言（规格见项目根目录 UI_STYLE.md）：
  - 深色半透明渐变面板 + 深色外描边 + 内高光线 + 投影；统一切角（CUT）与斜切（SLANT）；选手主题色作"队伍色"
  - 数字用"计分"字体（Segoe UI Black Italic，缺则 Arial Black / Impact），粗描边、渐变填充，计数有弹跳
  - 所有动画只取决于回放位置（帧号 k / kf、成绩榜的 end_t、片头/倒计时的 t），可任意回退/拖动
素材接口：assets/ui/ 下有对应 PNG 就用（见 assets/ui/README.md），没有就用这里的程序绘制版；
  字体可放 assets/ui/fonts/display_cn.(ttf|otf|ttc)（大标题中文）、num.(ttf|otf)（计分数字）覆盖。
性能：渐变/描边/面板/图标/静态卡片全部按参数缓存；每帧只拼数字（逐字形缓存后拼接）。
"""
import bisect
import logging
import math
import os
from collections import OrderedDict

import pygame

import evaluator
import physics
import viz_fx as fx
import viz_hud as hud
from viz_fx import mix, pf, px, shade

V = None                    # visualizer 模块（bind() 注入；visualizer 以 __main__ 运行时不能反向 import）
FPS = physics.FPS
PPM = physics.PPM
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(ROOT, "assets", "ui")


def bind(mod):
    global V
    V = mod


# ------------------------------------------------------------ 色板 --------
INK = (12, 14, 24)                    # 统一深色描边
BG = (12, 15, 26)
NAVY_T, NAVY_B = (44, 52, 88), (16, 19, 36)          # 面板渐变（上 → 下）
PANEL_A = 226
WHITE = (255, 255, 255)
MUTED = (168, 178, 204)
GOLD = (250, 205, 70)
GOLD_HI, GOLD_LO = (255, 244, 176), (226, 138, 22)
SILVER, BRONZE, STEEL = (214, 220, 232), (218, 138, 70), (122, 130, 150)
MEDAL = (GOLD, SILVER, BRONZE)
RED, RED_HI, RED_LO = (232, 42, 52), (255, 140, 120), (168, 14, 30)
PURPLE = (148, 92, 244)
TEAL = (36, 172, 150)
GO_HI, GO_LO = (214, 255, 150), (34, 186, 72)
EFFORT_COL = {"minimal": (96, 150, 120), "low": (84, 156, 112), "medium": (30, 150, 200), "mid": (30, 150, 200),
              "high": (238, 104, 36), "xhigh": (224, 50, 92), "max": (160, 72, 228), "thinking": (120, 96, 220),
              "低": (84, 156, 112), "中": (30, 150, 200), "高": (238, 104, 36), "超高": (224, 50, 92), "最高": (160, 72, 228)}
GRADE_COL = {"S": GOLD, "A": (242, 82, 58), "B": (58, 138, 242), "C": (64, 186, 104), "D": (156, 122, 92), "E": (112, 112, 124)}
GRADE_STARS = {"S": 3, "A": 2, "B": 1}

# 几何规格（720p 单位，用时经 px() 缩放）
OW = 2            # 面板描边
CUT = 7           # 切角
SLANT = 12        # 斜切
SHADOW = 3        # 投影偏移

# 动画时长（秒）
METER_POP = 0.28            # 成绩每过一个整米弹一下
RANK_HOLD = 12              # 名次变化要保持多少帧才生效（防止并排时来回跳）
RANK_POP, RANK_ARROW = 0.45, 1.2
FLOAT_SECONDS = 0.8         # 倒地瞬间的伤害数字只飘一次
BANNER_IN, BANNER_HOLD, BANNER_OUT = 0.25, 1.4, 0.3
COUNT_STEP = 0.5            # 3-2-1-GO 每拍（共 2 秒）
COUNT_TOTAL = 4 * COUNT_STEP


def _ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _ease_back(t, k=1.7):
    t = max(0.0, min(1.0, t)) - 1
    return 1 + (k + 1) * t ** 3 + k * t ** 2


def _ease_in(t):
    t = max(0.0, min(1.0, t))
    return t ** 3


# ------------------------------------------------------------ 字体 --------
_FONT_FILES = {
    "cn": ("msyhbd.ttc", "simhei.ttf", "msyh.ttc"),            # 中文粗体（名字、标签）
    "cnr": ("msyh.ttc", "simhei.ttf", "msyhbd.ttc"),           # 中文常规（模型名、说明）
    "disp": ("msyhbd.ttc", "simhei.ttf"),                      # 中文大标题（可被 assets/ui/fonts/display_cn.* 覆盖）
    "num": ("seguibli.ttf", "seguibl.ttf", "ariblk.ttf", "impact.ttf", "msyhbd.ttc"),     # 计分数字（斜体重黑）
    "numu": ("seguibl.ttf", "ariblk.ttf", "msyhbd.ttc"),        # 直立重黑（徽章、名次）
}
_FONT_OVERRIDE = {"disp": "display_cn", "num": "num"}
_fonts, _trims = {}, {}


def _font_path(kind):
    for stem in ([_FONT_OVERRIDE[kind]] if kind in _FONT_OVERRIDE else []):
        for ext in (".ttf", ".otf", ".ttc"):
            p = os.path.join(UI_DIR, "fonts", stem + ext)
            if os.path.exists(p):
                return p
    dirs = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")]
    for fn in _FONT_FILES[kind]:
        for d in dirs:
            p = os.path.join(d, fn)
            if os.path.exists(p):
                return p
    return None


def font(kind, size):
    key = (kind, size)
    f = _fonts.get(key)
    if f is None:
        p = _font_path(kind)
        try:
            f = pygame.font.Font(p, size) if p else None
        except (OSError, pygame.error):
            f = None
        f = f or fx.font(size, True)
        _fonts[key] = f
    return f


def _trim(kind, size):
    """同一字体同一字号的统一裁切行（去掉行距留白，保证基线一致）→ (top, height)。"""
    key = (kind, size)
    t = _trims.get(key)
    if t is None:
        ref = "0123456789ABCDEFGHIKLMNOPRSTUVWXYZ!.-+" if kind in ("num", "numu") else "国Ag8|"
        r = font(kind, size).render(ref, True, WHITE).get_bounding_rect()
        m = max(1, size // 14)
        t = _trims[key] = (max(0, r.y - m), r.h + 2 * m)
    return t


def _crop(img, top, hh):
    out = pygame.Surface((max(1, img.get_width()), hh), pygame.SRCALPHA)
    out.blit(img, (0, -top))
    return out


# ------------------------------------------------------------ 缓存工具 ----
class _LRU(OrderedDict):
    def __init__(self, cap):
        super().__init__()
        self.cap = cap

    def get_or(self, key, make):
        v = self.get(key)
        if v is None:
            v = self[key] = make()
            if len(self) > self.cap:
                self.popitem(last=False)
        else:
            self.move_to_end(key)
        return v


_static = {}                 # 不会膨胀的缓存（面板、图标、静态文字）
_dyn = _LRU(1500)            # 每帧变化的数字等


def cached(key, make):
    s = _static.get(key)
    if s is None:
        if len(_static) > 6000:
            _static.clear()
        s = _static[key] = make()
    return s


def vgrad(w, h, top, bot):
    """竖直渐变（RGBA），缓存。"""
    def make():
        top4 = top if len(top) == 4 else (*top, 255)
        bot4 = bot if len(bot) == 4 else (*bot, 255)
        col = pygame.Surface((1, max(1, h)), pygame.SRCALPHA)
        for y in range(max(1, h)):
            u = y / max(1, h - 1)
            col.set_at((0, y), tuple(int(a + (b - a) * u) for a, b in zip(top4, bot4)))
        return pygame.transform.scale(col, (max(1, w), max(1, h)))
    return cached(("vgrad", w, h, top, bot), make)


def _disk(r):
    if r <= 0:
        return []
    if r <= 3:
        return [(dx, dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1) if (dx or dy) and dx * dx + dy * dy <= r * r + 1]
    out = set()
    for rr in (r, r * 0.7, r * 0.4):
        n = max(10, int(2 * math.pi * rr * 1.3))
        for j in range(n):
            a = 2 * math.pi * j / n
            out.add((int(round(math.cos(a) * rr)), int(round(math.sin(a) * rr))))
    out.discard((0, 0))
    return sorted(out)


def tint(img, color):
    """带透明度的图 → 同形状的纯色剪影。"""
    s = img.copy()
    s.fill((0, 0, 0, 255), special_flags=pygame.BLEND_RGBA_MULT)
    s.fill((*color, 0), special_flags=pygame.BLEND_RGBA_ADD)
    return s


def glowed(img, color, r, strength=2):
    """外发光：剪影模糊后垫在下面。返回 (新图, 四周扩出的边距)。"""
    w, h = img.get_size()
    pad = r * 2
    g = pygame.Surface((w + 2 * pad, h + 2 * pad), pygame.SRCALPHA)
    g.fill((*color, 0))
    sil = tint(img, color)
    for _ in range(strength):
        g.blit(sil, (pad, pad))
    g = pygame.transform.gaussian_blur(g, max(1, r), False)
    g.blit(img, (pad, pad))
    return g, pad


# ------------------------------------------------------------ 描边文字 ----
def _layers(s, size, fill, kind, ow, oc, grad, sh):
    """→ (底层 = 投影 + 描边, 顶层 = 填充)，两张同尺寸；数字逐字形拼接时先铺所有底层再铺顶层，描边不压前一个字。"""
    f = font(kind, size)
    top, hh = _trim(kind, size)
    img = _crop(f.render(s, True, WHITE if grad else fill), top, hh)
    if grad:
        img.blit(vgrad(img.get_width(), hh, grad[0], grad[1]), (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    w = img.get_width()
    pad = ow
    W2, H2 = w + 2 * pad, hh + 2 * pad + sh
    back = pygame.Surface((W2, H2), pygame.SRCALPHA)
    if ow or sh:
        sil = _crop(f.render(s, True, oc), top, hh)
        offs = _disk(ow) + [(0, 0)]
        if sh:
            shs = _crop(f.render(s, True, (0, 0, 0)), top, hh)
            shs.set_alpha(150)
            for dx, dy in offs:
                back.blit(shs, (pad + dx, pad + dy + sh))
        for dx, dy in offs:
            back.blit(sil, (pad + dx, pad + dy))
    front = pygame.Surface((W2, H2), pygame.SRCALPHA)
    front.blit(img, (pad, pad))
    return back, front


def _defaults(size, ow, sh):
    if ow is None:
        ow = max(1, int(round(size / 10)))
    if sh is None:
        sh = max(1, ow // 2 + (1 if size >= pf(24) else 0))
    return ow, sh


def stext(s, size, fill=WHITE, kind="cn", ow=None, oc=INK, grad=None, sh=None):
    """粗描边 + 投影 + （可选）竖直渐变的文字，整串渲染并缓存（静态文字用）。"""
    ow, sh = _defaults(size, ow, sh)
    key = ("st", s, size, fill, kind, ow, oc, grad, sh)

    def make():
        back, front = _layers(s, size, fill, kind, ow, oc, grad, sh)
        back.blit(front, (0, 0))
        return back
    return cached(key, make) if len(s) <= 40 else make()


def ntext(s, size, fill=WHITE, kind="num", ow=None, oc=INK, grad=None, sh=None, track=0):
    """计分数字：逐字形缓存（描边/渐变只做一次），拼接结果进 LRU。每帧变化的成绩/扣分/时钟用这个。"""
    ow, sh = _defaults(size, ow, sh)
    key = ("nt", s, size, fill, kind, ow, oc, grad, sh, track)

    def make():
        f = font(kind, size)
        pieces = []
        x = 0
        for c in s:
            g = cached(("glyph", c, size, fill, kind, ow, oc, grad, sh), lambda c=c: _layers(c, size, fill, kind, ow, oc, grad, sh))
            m = f.metrics(c)
            adv = m[0][4] if m and m[0] else g[0].get_width() - 2 * ow
            pieces.append((x, g))
            x += adv + track
        x -= track
        last = pieces[-1][1][0].get_width() - 2 * ow if pieces else 0
        lx = pieces[-1][0] if pieces else 0
        w = max(x, lx + last) + 2 * ow
        h = pieces[0][1][0].get_height() if pieces else 1
        out = pygame.Surface((max(1, w), h), pygame.SRCALPHA)
        for px_, (b, _f) in pieces:
            out.blit(b, (px_, 0))
        for px_, (_b, f_) in pieces:
            out.blit(f_, (px_, 0))
        return out
    return _dyn.get_or(key, make)


def fit_stext(s, max_w, size, min_size, **kw):
    """先缩字号（不低于 min_size），还放不下就截断加省略号。"""
    fs = size
    img = stext(s, fs, **kw)
    while img.get_width() > max_w and fs > min_size:
        fs -= 1
        img = stext(s, fs, **kw)
    if img.get_width() <= max_w:
        return img
    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if stext(s[:mid].rstrip() + "…", fs, **kw).get_width() <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return stext((s[:lo].rstrip() + "…") if lo else "…", fs, **kw)


# ------------------------------------------------------------ 面板 --------
def _shape(w, h, cuts, slant, ox=0.0, oy=0.0):
    """切角矩形（四角切角 tl, tr, br, bl）再整体斜切（上沿右移 slant）。"""
    tl, tr, br, bl = cuts
    w0 = w - slant
    raw = [(tl, 0), (w0 - tr, 0), (w0, tr), (w0, h - br), (w0 - br, h), (bl, h), (0, h - bl), (0, tl)]
    pts = []
    for x, y in raw:
        p = (ox + x + (slant * (1 - y / h) if h else 0), oy + y)
        if not pts or abs(p[0] - pts[-1][0]) > 1e-6 or abs(p[1] - pts[-1][1]) > 1e-6:
            pts.append(p)
    return pts


def _inner(w, h, cuts, slant, ow):
    ih = h - 2 * ow
    if ih <= 1:
        return _shape(w, h, cuts, slant)
    sl = slant * ih / h
    iw = (w - slant) - 2 * ow + sl
    cs = tuple(max(0.0, c - ow * 0.6) for c in cuts)
    return _shape(iw, ih, cs, sl, ow + slant * ow / h, ow)


def plate(w, h, top=NAVY_T, bot=NAVY_B, alpha=PANEL_A, ow=None, oc=INK, cut=None, slant=0, cuts=None,
          hi=True, shadow=True, band=None, gloss=True):
    """游戏面板：投影 + 深色外描边 + 渐变填充（可带顶部队伍色条 band=(色, 高)）+ 上半光泽 + 内高光线。
    返回 Surface（尺寸 = (w + 投影, h + 投影)，左上角对齐面板左上角）。"""
    ow = px(OW) if ow is None else ow
    cut = px(CUT) if cut is None else cut
    cuts = cuts or (cut, cut, cut, cut)
    w, h = max(4, int(w)), max(4, int(h))
    key = ("plate", w, h, top, bot, alpha, ow, oc, cuts, slant, hi, shadow, band, gloss)

    def make():
        sd = px(SHADOW) if shadow else 0
        S = pygame.Surface((w + sd, h + sd), pygame.SRCALPHA)
        outer = _shape(w, h, cuts, slant)
        inner = _inner(w, h, cuts, slant, ow)
        if sd:
            pygame.draw.polygon(S, (0, 0, 0, 105), [(x + sd, y + sd) for x, y in outer])
            pygame.draw.polygon(S, (0, 0, 0, 0), outer)
        fill = vgrad(w, h, (*top, alpha), (*bot, alpha)).copy()
        if band:
            pygame.draw.rect(fill, (*band[0], 255), (0, 0, w, ow + band[1]))
        mask = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.polygon(mask, (255, 255, 255, 255), inner)
        fill.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        S.blit(fill, (0, 0))
        over = pygame.Surface((w, h), pygame.SRCALPHA)
        if gloss:
            pygame.draw.polygon(over, (255, 255, 255, 24), inner)
            over.fill((0, 0, 0, 0), (0, int(h * 0.46), w, h))
        if hi and len(inner) >= 2:
            ys = min(p[1] for p in inner)
            top_pts = sorted((p for p in inner if abs(p[1] - ys) < 0.5), key=lambda p: p[0])
            if len(top_pts) >= 2:
                y0 = ys + max(1, ow // 2)
                pygame.draw.line(over, (255, 255, 255, 120), (top_pts[0][0] + 1, y0), (top_pts[-1][0] - 1, y0), max(1, px(1)))
        S.blit(over, (0, 0))
        if ow:
            ring = pygame.Surface((w, h), pygame.SRCALPHA)
            pygame.draw.polygon(ring, (*oc, 255), outer)
            pygame.draw.polygon(ring, (0, 0, 0, 0), inner)
            S.blit(ring, (0, 0))
        return S
    return cached(key, make)


def chip(s, size, bg, fg=WHITE, kind="numu", slant=None, pad=None, h=None):
    """小标签（斜切）：档位、倍速、状态。返回 Surface（无投影）。"""
    key = ("chip", s, size, bg, fg, kind, slant, pad, h)

    def make():
        img = stext(s, size, fg, kind, ow=max(1, px(1)), oc=shade(bg, 0.42), sh=0)
        hh = h or img.get_height() + px(2)
        sl = int(hh * 0.32) if slant is None else slant
        pd = px(6) if pad is None else pad
        w = img.get_width() + 2 * pd + sl
        pl = plate(w, hh, mix(bg, WHITE, 0.28), shade(bg, 0.78), 255, ow=max(1, px(1)), cut=px(2), slant=sl, shadow=False)
        out = pl.copy()
        out.blit(img, ((w - img.get_width()) // 2, (hh - img.get_height()) // 2))
        return out
    return cached(key, make)


def effort_chip(e, size):
    return chip(e.upper() if e.isascii() else e, size, EFFORT_COL.get(e.lower(), STEEL))


# ------------------------------------------------------------ 外部素材 ----
def ui_img(name, size):
    """assets/ui/<name>.png 存在就按 size=(w, h) 等比缩放进框内后返回，否则 None（调用方回退到程序绘制）。"""
    def make():
        p = os.path.join(UI_DIR, name + ".png")
        if not os.path.exists(p):
            return False
        try:
            img = pygame.image.load(p)
            img = img.convert_alpha() if pygame.display.get_surface() is not None else img
        except pygame.error:
            return False
        w, h = size
        sc = min(w / img.get_width(), h / img.get_height())
        return pygame.transform.smoothscale(img, (max(1, round(img.get_width() * sc)), max(1, round(img.get_height() * sc))))
    r = cached(("ui_img", name, size), make)
    return r or None


# ------------------------------------------------------------ 图标 --------
def _star_pts(cx, cy, r, n=5, inner=0.45, rot=-math.pi / 2):
    return [(cx + math.cos(rot + j * math.pi / n) * (r if j % 2 == 0 else r * inner),
             cy + math.sin(rot + j * math.pi / n) * (r if j % 2 == 0 else r * inner)) for j in range(2 * n)]


def icon(kind, sz, col=None):
    """手绘游戏小图标（缓存）：fall 爆炸星"！"、hurdle 栏架、flag 方格旗、warn 警示三角、crown 皇冠、
    up/down 名次箭头、star/star0 星星、bump 减速带。"""
    sz = max(6, int(sz))

    def make():
        s = pygame.Surface((sz, sz), pygame.SRCALPHA)
        c = sz / 2
        ow = max(1, int(round(sz / 12)))
        if kind == "fall":
            pts = _star_pts(c, c, sz * 0.49, 8, 0.6, 0.2)
            pygame.draw.polygon(s, INK, pts)
            pygame.draw.polygon(s, col or (255, 96, 60), _star_pts(c, c, sz * 0.49 - ow * 1.4, 8, 0.6, 0.2))
            pygame.draw.polygon(s, (255, 214, 70), _star_pts(c, c, sz * 0.26, 8, 0.6, 0.2))
            bw = max(1, int(sz * 0.12))
            pygame.draw.rect(s, WHITE, (c - bw / 2, c - sz * 0.24, bw, sz * 0.3))
            pygame.draw.rect(s, WHITE, (c - bw / 2, c + sz * 0.12, bw, bw))
        elif kind == "hurdle":
            for x in (0.2, 0.8):
                pygame.draw.rect(s, INK, (sz * x - ow * 1.5, sz * 0.25, ow * 3, sz * 0.72))
                pygame.draw.rect(s, (230, 232, 240), (sz * x - ow * 0.5, sz * 0.3, ow, sz * 0.64))
            bar = pygame.Rect(int(sz * 0.06), int(sz * 0.18), int(sz * 0.88), max(3, int(sz * 0.28)))
            pygame.draw.rect(s, INK, bar)
            inner = bar.inflate(-2 * ow, -2 * ow)
            n = 4
            for j in range(n):
                pygame.draw.rect(s, (230, 46, 46) if j % 2 == 0 else WHITE,
                                 (inner.x + inner.w * j // n, inner.y, inner.w // n + 1, inner.h))
        elif kind == "flag":               # 浅色旗杆 + 浅色描边的方格旗：深色、金色底上都看得清
            pygame.draw.rect(s, INK, (sz * 0.12 - ow * 1.5, sz * 0.04, ow * 3 + 1, sz * 0.94))
            pygame.draw.rect(s, (226, 228, 236), (sz * 0.12 - ow * 0.5, sz * 0.06, ow + 1, sz * 0.9))
            fr = pygame.Rect(int(sz * 0.2), int(sz * 0.06), int(sz * 0.76), int(sz * 0.54))
            pygame.draw.rect(s, INK, fr.inflate(2 * ow, 2 * ow))
            pygame.draw.rect(s, (240, 240, 246), fr)
            inner = fr.inflate(-2 * ow, -2 * ow)
            cw, ch = inner.w / 3, inner.h / 2
            for j in range(3):
                for r in range(2):
                    pygame.draw.rect(s, (20, 20, 24) if (j + r) % 2 else WHITE,
                                     (inner.x + j * cw, inner.y + r * ch, math.ceil(cw), math.ceil(ch)))
        elif kind == "warn":
            pts = [(c, sz * 0.04), (sz * 0.98, sz * 0.94), (sz * 0.02, sz * 0.94)]
            pygame.draw.polygon(s, INK, pts)
            k = ow * 1.8
            pygame.draw.polygon(s, (255, 206, 40), [(c, sz * 0.04 + k * 1.6), (sz * 0.98 - k * 1.3, sz * 0.94 - k * 0.8),
                                                    (sz * 0.02 + k * 1.3, sz * 0.94 - k * 0.8)])
            bw = max(1, int(sz * 0.13))
            pygame.draw.rect(s, INK, (c - bw / 2, sz * 0.34, bw, sz * 0.32))
            pygame.draw.rect(s, INK, (c - bw / 2, sz * 0.72, bw, bw))
        elif kind == "crown":
            pts = [(sz * 0.08, sz * 0.82), (sz * 0.08, sz * 0.3), (sz * 0.3, sz * 0.55), (c, sz * 0.12),
                   (sz * 0.7, sz * 0.55), (sz * 0.92, sz * 0.3), (sz * 0.92, sz * 0.82)]
            pygame.draw.polygon(s, INK, pts)
            ins = [(x + (c - x) * 0.18, y + (sz * 0.55 - y) * 0.18) for x, y in pts]
            pygame.draw.polygon(s, GOLD, ins)
            pygame.draw.rect(s, GOLD_LO, (sz * 0.2, sz * 0.62, sz * 0.6, max(1, sz * 0.1)))
        elif kind in ("up", "down"):
            up = kind == "up"
            pts = [(c, sz * 0.08), (sz * 0.94, sz * 0.62), (sz * 0.66, sz * 0.62), (sz * 0.66, sz * 0.94),
                   (sz * 0.34, sz * 0.94), (sz * 0.34, sz * 0.62), (sz * 0.06, sz * 0.62)]
            if not up:
                pts = [(x, sz - y) for x, y in pts]
            pygame.draw.polygon(s, INK, pts)
            ins = [(x + (c - x) * 0.22, y + (c - y) * 0.22) for x, y in pts]
            pygame.draw.polygon(s, (90, 230, 110) if up else (250, 80, 70), ins)
        elif kind in ("star", "star0"):
            pygame.draw.polygon(s, INK, _star_pts(c, c * 1.05, sz * 0.5))
            pygame.draw.polygon(s, GOLD if kind == "star" else (70, 76, 96), _star_pts(c, c * 1.05, sz * 0.5 - ow * 1.6))
            if kind == "star":
                pygame.draw.polygon(s, GOLD_HI, _star_pts(c - sz * 0.04, c * 0.98, sz * 0.2))
        elif kind == "bump":
            r = pygame.Rect(0, int(sz * 0.45), sz, int(sz * 0.4))
            pygame.draw.rect(s, INK, r, border_radius=r.h // 2)
            ir = r.inflate(-2 * ow, -2 * ow)
            pygame.draw.rect(s, (250, 205, 40), ir, border_radius=ir.h // 2)
            for j in range(3):
                x = ir.x + ir.w * (j + 0.3) / 3
                pygame.draw.line(s, (30, 30, 30), (x, ir.bottom - 1), (x + ir.h * 0.6, ir.y), max(1, ow))
        return s
    return cached(("icon", kind, sz, col), make)


def medal(d, rank, ring=None):
    """名次奖牌（结算界面）：前三金银铜圆牌 + 丝带，之后钢灰盾牌。assets/ui/medal_<名次>.png 可替换。"""
    alt = ui_img(f"medal_{rank + 1}", (d, d))
    if alt is not None:
        return alt

    def make():
        s = pygame.Surface((d, d), pygame.SRCALPHA)
        c = d / 2
        col = MEDAL[rank] if rank < 3 else STEEL
        ow = max(1, d // 18)
        if rank < 3:
            rc = ring or (200, 50, 60)
            for sgn, cc in ((-1, rc), (1, shade(rc, 0.72))):
                pts = [(c + sgn * d * 0.06, 0), (c + sgn * d * 0.34, 0), (c + sgn * d * 0.2, d * 0.5), (c - sgn * d * 0.06, d * 0.5)]
                pygame.draw.polygon(s, INK, pts)
                pygame.draw.polygon(s, cc, [(x + (c - x) * 0.12, y + 1) for x, y in pts])
            r = d * 0.36
            cy = d - r - ow
            pygame.draw.circle(s, INK, (c, cy), r)
            g = vgrad(int(2 * r), int(2 * r), mix(col, WHITE, 0.45), shade(col, 0.72))
            m = pygame.Surface(g.get_size(), pygame.SRCALPHA)
            pygame.draw.circle(m, WHITE, (r, r), r - ow)
            gg = g.copy()
            gg.blit(m, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
            s.blit(gg, (c - r, cy - r))
            pygame.draw.circle(s, shade(col, 0.62), (c, cy), r * 0.76, max(1, ow))
            num = stext(str(rank + 1), int(r * 1.15), WHITE, "numu", ow=max(1, ow), oc=shade(col, 0.4), sh=0)
            s.blit(num, (c - num.get_width() / 2, cy - num.get_height() / 2))
        else:
            pts = [(d * 0.14, d * 0.1), (d * 0.86, d * 0.1), (d * 0.86, d * 0.55), (c, d * 0.96), (d * 0.14, d * 0.55)]
            pygame.draw.polygon(s, INK, pts)
            ins = [(x + (c - x) * 0.14, y + (d * 0.5 - y) * 0.14) for x, y in pts]
            pygame.draw.polygon(s, col, ins)
            num = stext(str(rank + 1), int(d * 0.42), WHITE, "numu", ow=max(1, ow), oc=shade(col, 0.4), sh=0)
            s.blit(num, (c - num.get_width() / 2, d * 0.46 - num.get_height() / 2))
        return s
    return cached(("medal", d, rank, ring), make)


ORD = ("ST", "ND", "RD")


def rank_shield(h, rank):
    """实时名次盾牌：1ST/2ND/3RD 金银铜，其余钢灰。assets/ui/rank_<名次>.png 可替换。"""
    w = int(h * 0.86)
    alt = ui_img(f"rank_{rank + 1}", (w, h))
    if alt is not None:
        return alt

    def make():
        s = pygame.Surface((w, h), pygame.SRCALPHA)
        col = MEDAL[rank] if rank < 3 else STEEL
        c = w / 2
        ow = max(1, h // 16)
        pts = [(w * 0.04, h * 0.04), (w * 0.96, h * 0.04), (w * 0.96, h * 0.58), (c, h * 0.98), (w * 0.04, h * 0.58)]
        pygame.draw.polygon(s, INK, pts)
        ins = [(x + (c - x) * (2.2 * ow / w), y + (h * 0.45 - y) * (2.2 * ow / h)) for x, y in pts]
        g = vgrad(w, h, mix(col, WHITE, 0.5), shade(col, 0.66)).copy()
        m = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.polygon(m, WHITE, ins)
        g.blit(m, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        s.blit(g, (0, 0))
        gl = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.polygon(gl, (255, 255, 255, 60), ins)
        gl.fill((0, 0, 0, 0), (0, int(h * 0.34), w, h))
        s.blit(gl, (0, 0))
        dark = shade(col, 0.36) if rank < 3 else (30, 34, 46)
        num = stext(str(rank + 1), int(h * 0.5), WHITE, "num", ow=max(1, ow), oc=dark, sh=0)
        suf = stext(ORD[rank] if rank < 3 else "TH", int(h * 0.2), WHITE, "numu", ow=max(1, ow // 2 + 1), oc=dark, sh=0)
        tw = num.get_width() + suf.get_width() - ow
        x0 = c - tw / 2
        y0 = h * 0.40 - num.get_height() / 2
        s.blit(num, (x0, y0))
        s.blit(suf, (x0 + num.get_width() - ow * 2, y0 + num.get_height() * 0.1))
        return s
    return cached(("shield", h, rank), make)


def grade_badge(d, g):
    """评级徽章：六边形 + 大字母，S 带金色光晕。assets/ui/grade_<字母>.png 可替换。返回 (图, 光晕边距)。"""
    alt = ui_img(f"grade_{g}", (d, d))
    if alt is not None:
        return alt, 0

    def make():
        s = pygame.Surface((d, d), pygame.SRCALPHA)
        col = GRADE_COL.get(g, STEEL)
        c = d / 2
        ow = max(1, d // 16)
        hexa = [(c + math.cos(math.pi / 6 + j * math.pi / 3) * d * 0.49, c + math.sin(math.pi / 6 + j * math.pi / 3) * d * 0.49) for j in range(6)]
        pygame.draw.polygon(s, INK, hexa)
        inn = [(c + (x - c) * (1 - 2.4 * ow / d), c + (y - c) * (1 - 2.4 * ow / d)) for x, y in hexa]
        gr = vgrad(d, d, mix(col, WHITE, 0.55), shade(col, 0.62)).copy()
        m = pygame.Surface((d, d), pygame.SRCALPHA)
        pygame.draw.polygon(m, WHITE, inn)
        gr.blit(m, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        s.blit(gr, (0, 0))
        in2 = [(c + (x - c) * 0.78, c + (y - c) * 0.78) for x, y in hexa]
        pygame.draw.polygon(s, mix(col, WHITE, 0.7), in2, max(1, ow // 2 + 1))
        L = stext(g, int(d * 0.56), WHITE, "num", ow=max(1, ow), oc=shade(col, 0.32), sh=max(1, ow // 2))
        s.blit(L, (c - L.get_width() / 2 - d * 0.02, c - L.get_height() / 2))
        if g == "S":
            return glowed(s, (255, 214, 90), max(2, d // 10), 2)
        return s, 0
    return cached(("grade", d, g), make)


def portrait_or_badge(R, i, d, ring_w=None):
    """头像徽章：有头像 = 圆形头像（队伍色粗环 + 深色外描边）；没有 = 队伍色圆牌 + 大号赛道号。"""
    ch, color = R.chars[i], R.colors[i]
    rw = ring_w or max(px(2), d // 14)
    key = ("pbadge", i, d, rw, ch.key if ch is not None and ch.head is not None else None, color)

    def make():
        s = pygame.Surface((d + 2, d + 2), pygame.SRCALPHA)
        c = (d / 2 + 1, d / 2 + 1)
        ow = max(1, px(1) + d // 40)
        pygame.draw.circle(s, INK, c, d / 2)
        if ch is not None and ch.head is not None:
            img = ch.portrait(int(d - 2 * ow), color, rw)
            s.blit(img, (1 + ow, 1 + ow))
        else:
            g = vgrad(d, d, mix(color, WHITE, 0.35), shade(color, 0.7)).copy()
            m = pygame.Surface((d, d), pygame.SRCALPHA)
            pygame.draw.circle(m, WHITE, (d / 2, d / 2), d / 2 - ow)
            g.blit(m, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
            s.blit(g, (1, 1))
            pygame.draw.circle(s, mix(color, WHITE, 0.6), c, d / 2 - ow - max(1, rw // 2), max(1, px(1)))
            num = stext(str(i + 1), int(d * 0.5), WHITE, "num", oc=INK)
            s.blit(num, (c[0] - num.get_width() / 2, c[1] - num.get_height() / 2))
        return s
    return cached(key, make)


# ------------------------------------------------------------ 预计算 ------
def prepare(R):
    """实时名次（带迟滞，名次变化要保持 RANK_HOLD 帧才生效）和"过整米"弹跳帧。只依赖录制数据。"""
    n, N = R.n_frames, len(R.lanes)
    fins = [L["finish_k"] for L in R.lanes]
    fts = [L["result"]["finish_time"] for L in R.lanes]

    def ranks_at(k):
        keys = []
        for i, L in enumerate(R.lanes):
            done = fins[i] is not None and k >= fins[i]
            keys.append((0 if done else 1, fts[i] if done else 0.0, -round(L["frames"][k]["score"], 2)))
        return tuple(sum(1 for j in range(N) if keys[j] < keys[i]) for i in range(N))

    cur, cand, run = ranks_at(0), None, 0
    R.g_rank, R.g_rank_chg = [], [[] for _ in range(N)]
    for k in range(n):
        o = ranks_at(k)
        if o != cur:
            run = run + 1 if o == cand else 1
            cand = o
            if run >= RANK_HOLD or k == n - 1:
                for i in range(N):
                    if o[i] != cur[i]:
                        R.g_rank_chg[i].append((k, o[i] - cur[i]))
                cur, cand, run = o, None, 0
        else:
            cand, run = None, 0
        R.g_rank.append(cur)
    R.g_rank_chg_k = [[c[0] for c in chg] for chg in R.g_rank_chg]
    # 排名从有人跑出 0.1m 之后才显示（开局大家都是 0，名次没有意义）
    R.g_rank_from = next((k for k in range(n) if max(L["frames"][k]["score"] for L in R.lanes) >= 0.1), n)
    R.g_meter = []
    for L in R.lanes:
        best, ks = 0, []
        for k, f in enumerate(L["frames"]):
            m = math.floor(f["score"])
            if m > best:
                best = m
                ks.append(k)
        R.g_meter.append(ks)
    R._g_cd_base = None
    R._g_end_t = None
    R._g_float = {}


def _last_le(keys, k):
    j = bisect.bisect_right(keys, k) - 1
    return j


# ------------------------------------------------------------ 整帧 --------
def draw_frame(R, surf, t, ui):
    """Renderer.draw 的游戏版（赛道内容仍由 Renderer._draw_lane 画，其中 HUD/气泡/横幅再转回本模块）。"""
    W, H, TOP = V.W, V.H, V.TOP
    k, fr = R.split(t)
    lay = R.layout(k, ui)
    R.last_layout = lay
    surf.fill(BG)
    if ui.layout == "grid" and len(R.lanes) == 3 and ui.focus is None and not ui.follow:
        cw, ch = lay[0][1].w, lay[0][1].h
        _placeholder(R, surf, pygame.Rect(lay[0][1].x + cw, TOP + ch, lay[1][1].w, lay[2][1].h))
    R._g_end_t = ui.end_t
    for i, rect in lay:
        R._draw_lane(surf, i, rect, k, fr, ui)
    for i, rect in lay:                             # 冲线横幅最后画：不被选手卡、名次盾牌盖住
        surf.set_clip(rect)
        draw_banner(R, surf, rect, R.lanes[i], i, k, fr, ui)
        surf.set_clip(None)
    for i, rect in lay:
        pygame.draw.rect(surf, INK, rect, px(2))
        pygame.draw.rect(surf, R.colors[i], (rect.x + px(2), rect.y + px(2), px(4), rect.h - px(4)))
    if len(lay) > 1 and (ui.focus is not None or ui.follow):
        i, rect = lay[0]
        pygame.draw.rect(surf, R.colors[i], rect, px(3))
        tag = "焦点赛道 · 再次点击还原" if ui.focus is not None else "追拍 · 当前领先"
        img = chip(tag, px(13), R.colors[i], kind="cn")
        surf.blit(img, (rect.right - img.get_width() - px(10), rect.bottom - img.get_height() - px(8)))
    if V.SUB_BAND:                                  # 底部字幕带（--sub-band）：与顶栏同款深色斜纹底，上沿金线
        surf.blit(_sub_bg(V.W, V.SUB_BAND), (0, V.H - V.SUB_BAND))
    draw_top(R, surf, k, fr, ui)
    if ui.end_t is not None:
        draw_board(R, surf, ui.end_t)
    if ui.hover is not None:
        draw_hover(R, surf, ui.hover, k)
    if ui.show_help:
        draw_help(R, surf)
    if ui.toast:
        img = stext(ui.toast[0], px(16), WHITE, "cn")
        w, h = img.get_width() + px(40), img.get_height() + px(14)
        x, y = (W - w) // 2, H - h - px(20)
        surf.blit(plate(w, h, slant=px(10), band=(GOLD, px(2))), (x, y))
        surf.blit(img, (x + (w - img.get_width()) // 2 + px(3), y + (h - img.get_height()) // 2))


def _placeholder(R, surf, rect):
    pygame.draw.rect(surf, (22, 26, 44), rect)
    for j in range(-rect.h, rect.w, px(28)):
        pygame.draw.line(surf, (28, 33, 54), (rect.x + j, rect.bottom), (rect.x + j + rect.h, rect.y), px(10))
    t1 = stext("AI 运动会", px(34), WHITE, "disp", grad=(GOLD_HI, GOLD_LO), ow=px(3))
    surf.blit(t1, (rect.centerx - t1.get_width() // 2, rect.centery - px(44)))
    sub = f"{physics.TRACKS[R.track]['label']} · {fx.MODE_LABEL.get(R.mode, R.mode)}"
    img = chip(sub, px(16), (60, 70, 110), kind="cn")
    surf.blit(img, (rect.centerx - img.get_width() // 2, rect.centery + px(10)))


# ------------------------------------------------------------ 顶栏 --------
def _top_bg(W, TOP):
    def make():
        s = vgrad(W, TOP, (30, 36, 64), (10, 12, 24)).copy()
        for x in range(-TOP, W, px(22)):
            pygame.draw.line(s, (34, 40, 70), (x, TOP), (x + TOP, 0), px(6))
        s = s.convert() if pygame.display.get_surface() is not None else s
        pygame.draw.rect(s, INK, (0, TOP - px(4), W, px(4)))
        pygame.draw.rect(s, GOLD_LO, (0, TOP - px(3), W, px(1)))
        return s
    return cached(("topbg", W, TOP), make)


def _sub_bg(W, BH):
    def make():
        s = vgrad(W, BH, (10, 12, 24), (22, 26, 48)).copy()
        for x in range(-BH, W, px(22)):
            pygame.draw.line(s, (26, 30, 56), (x, BH), (x + BH, 0), px(6))
        pygame.draw.rect(s, INK, (0, 0, W, px(4)))
        pygame.draw.rect(s, GOLD_LO, (0, px(3), W, px(1)))
        return s
    return cached(("subbg", W, BH), make)


def draw_top(R, surf, k, fr, ui):
    W, TOP = V.W, V.TOP
    surf.blit(_top_bg(W, TOP), (0, 0))
    # 左：赛事标牌（金色斜切）+ 比赛时钟 + 状态标签
    title = f"{physics.TRACKS[R.track]['label']} · {fx.MODE_LABEL.get(R.mode, R.mode)}"
    timg = stext(title, px(15), INK, "cn", ow=0, sh=0)
    tw, th = timg.get_width() + px(30), px(24)
    surf.blit(plate(tw, th, GOLD_HI, GOLD_LO, 255, slant=px(10), ow=px(2), cut=px(3)), (px(10), px(5)))
    surf.blit(timg, (px(10) + (tw - timg.get_width()) // 2 + px(3), px(5) + (th - timg.get_height()) // 2))
    clock = ntext(f"{k / FPS:05.2f}", px(22), WHITE, ow=px(2))
    cy = px(31)
    surf.blit(clock, (px(12), cy))
    x = px(12) + clock.get_width() + px(2)
    tot = ntext(f"/{R.sim_time:g}s", px(13), MUTED, ow=px(1), sh=0)
    surf.blit(tot, (x, cy + clock.get_height() - tot.get_height() - px(3)))
    x += tot.get_width() + px(8)
    by = cy + px(3)
    ch_h = px(19)

    def put(img):
        nonlocal x
        surf.blit(img, (x, by))
        x += img.get_width() + px(4)

    put(chip(f"{round(ui.speed, 2):g}x", px(12), (70, 82, 112), h=ch_h))
    if ui.paused:
        put(chip("暂停", px(12), (200, 60, 60), kind="cn", h=ch_h))
    if ui.slow_active:
        a = 0.5 + 0.5 * math.sin(k * 0.35)
        put(chip("慢动作 SLOW", px(12), mix(PURPLE, (190, 140, 255), a * 0.35), kind="cn", h=ch_h))
    if ui.follow:
        put(chip("追拍", px(12), TEAL, kind="cn", h=ch_h))
    if ui.show_hud and ui.interactive:
        surf.blit(fx.text("H 帮助", px(12), (130, 138, 160)), (x + px(2), by + px(2)))
    draw_minimap(R, surf, k, fr)
    draw_timeline(R, surf, k + fr, ui)


MAP_X0 = 440


def _map_geom(W):
    x0, x1 = px(MAP_X0), W - px(52)
    base = px(37)
    return x0, x1, base


def _map_bg(R, W):
    """赛道缩略图底图：地形剖面（纵向放大），按区段上色，减速带/栏架小图标，起终点。缓存。"""
    def make():
        x0, x1, base = _map_geom(W)
        span = physics.TRACK_LENGTH
        h = px(58)
        s = pygame.Surface((x1 - x0 + px(40), h), pygame.SRCALPHA)
        ox = px(14)
        vh = px(14) / max(0.3, max(physics.terrain_height(x / 10, R.track) for x in range(0, int(span * 10) + 1)))

        def P(xm, ym=None):
            ym = physics.terrain_height(xm, R.track) if ym is None else ym
            return (ox + (x1 - x0) * xm / span, base - ym * vh)
        zones = physics.TRACKS[R.track]["zones"]
        thick = px(6)
        for (za, zn), (zb, _n) in zip(zones, zones[1:]):
            xs = [za] + [xx for xx, _ in physics.TRACKS[R.track]["terrain"] if za < xx < zb] + [zb]
            top = [P(xx) for xx in xs]
            bot = [(x_, y_ + thick) for x_, y_ in reversed(top)]
            col = fx.ZONE_COLORS.get(zn, (120, 120, 120))
            pygame.draw.polygon(s, INK, [(x_, y_ - px(2)) for x_, y_ in top] + [(x_, y_ + px(2)) for x_, y_ in bot])
            pygame.draw.polygon(s, shade(col, 0.82), top + bot)
            pygame.draw.lines(s, mix(col, WHITE, 0.45), False, top, px(2))
        for m in range(0, int(span) + 1, 2):
            xx = P(m, 0)[0]
            img = fx.text(str(m), px(8), (150, 158, 180), True)
            s.blit(img, (xx - img.get_width() // 2, base + thick))
        sz = px(12)
        for bx, _bh, _bw in physics.TRACKS[R.track]["bumps"]:
            xx, yy = P(bx)
            s.blit(icon("bump", sz), (xx - sz / 2, yy - sz * 0.8))
        for hx, _hh, _hw in physics.TRACKS[R.track]["hurdles"]:
            xx, yy = P(hx)
            s.blit(icon("hurdle", sz), (xx - sz / 2, yy - sz * 0.95))
        fxp = P(span)
        s.blit(icon("flag", px(20)), (fxp[0] - px(3), fxp[1] - px(22)))
        st = P(0)
        pygame.draw.line(s, WHITE, (st[0], st[1] - px(6)), (st[0], st[1] + thick), px(2))
        return s, ox, vh
    return cached(("mapbg", R.track, W), make)


def draw_minimap(R, surf, k, fr):
    W = V.W
    x0, x1, base = _map_geom(W)
    bg, ox, vh = _map_bg(R, W)
    surf.blit(bg, (x0 - ox, 0))
    span = physics.TRACK_LENGTH
    rr, step = px(10), px(23)
    marks = []
    for i, L in enumerate(R.lanes):
        xm = max(-0.6, min(span + 0.6, V.frame_val(L["frames"], k, fr, "x")))
        marks.append((x0 + (x1 - x0) * xm / span, i, xm))
    marks.sort()
    clusters = []
    for m in marks:
        if clusters and m[0] - clusters[-1][-1][0] < step:
            clusters[-1].append(m)
        else:
            clusters.append([m])
    for x, i, xm in marks:          # 真实位置：地形上的队伍色小三角
        yy = base - physics.terrain_height(max(0.0, min(span, xm)), R.track) * vh
        pts = [(x - px(4), yy - px(7)), (x + px(4), yy - px(7)), (x, yy - px(1))]
        pygame.draw.polygon(surf, INK, [(a - (x - a) * 0.3 if a != x else a, b - px(1)) for a, b in pts])
        pygame.draw.polygon(surf, R.colors[i], pts)
    rank = R.g_rank[k] if len(R.lanes) >= 2 and k >= R.g_rank_from else None
    slots, prev_right = [], -1e9            # 头像标记：靠得近的并成一排；先从左往右排，再从右往左夹回画面内
    for cl in clusters:
        cx = sum(m[0] for m in cl) / len(cl)
        w = len(cl) * step
        left = max(x0 - px(14), cx - w / 2, prev_right + px(2))
        prev_right = left + w
        slots += [(left + step / 2 + j * step, m) for j, m in enumerate(cl)]
    limit = x1 + px(10)
    for j in range(len(slots) - 1, -1, -1):
        bx = min(slots[j][0], limit - step / 2)
        slots[j] = (bx, slots[j][1])
        limit = bx - step / 2 - px(2)
    y = px(17)
    for bx, (x, i, xm) in slots:
        bx = int(bx)
        yy = base - physics.terrain_height(max(0.0, min(span, xm)), R.track) * vh
        pygame.draw.line(surf, INK, (bx, y + rr), (x, yy - px(7)), px(3))
        pygame.draw.line(surf, R.colors[i], (bx, y + rr), (x, yy - px(7)), px(1))
        L = R.lanes[i]
        ring = R.colors[i]
        if L["down"][k]:
            ring = RED if (k // 10) % 2 == 0 else (255, 200, 60)
        elif L["finish_k"] is not None and k >= L["finish_k"]:
            ring = GOLD
        pygame.draw.circle(surf, INK, (bx, y), rr + px(2))
        pygame.draw.circle(surf, ring, (bx, y), rr + px(1))
        img = portrait_or_badge(R, i, 2 * rr - px(1), ring_w=px(1))
        surf.blit(img, (bx - img.get_width() // 2, y - img.get_height() // 2))
        if rank is not None and rank[i] == 0:
            cr = icon("crown", px(11))
            surf.blit(cr, (bx - rr - px(3), y - rr - px(5)))


def draw_timeline(R, surf, kf, ui):
    r = R.timeline_rect()
    p1, p2 = px(1), px(2)
    groove = r.inflate(px(4), px(4))
    pygame.draw.rect(surf, INK, groove, border_radius=groove.h // 2)
    pygame.draw.rect(surf, (40, 46, 70), r, border_radius=r.h // 2)
    n1 = max(1, R.n_frames - 1)
    frac = kf / n1
    fw = max(r.h, int(r.w * frac))
    surf.blit(_bar_fill(fw, r.h), r.topleft)
    for a, b in R.slow_windows:
        xa = r.x + r.w * max(0, a) / n1
        xb = r.x + r.w * min(n1, b) / n1
        pygame.draw.line(surf, PURPLE, (xa, r.bottom + px(4)), (xb, r.bottom + px(4)), px(3))
    sz = px(11)
    for ke, i, kind in R.events:
        x = r.x + r.w * ke / n1
        ic = icon({"fall": "fall", "hurdle": "hurdle", "finish": "flag"}[kind], sz)
        pygame.draw.line(surf, R.colors[i], (x, r.y - p2), (x, r.y - px(4)), p2)
        surf.blit(ic, (x - sz / 2, r.y - px(4) - sz))
    hx = r.x + r.w * frac
    d = px(9)
    pts = [(hx, r.centery - d), (hx + d, r.centery), (hx, r.centery + d), (hx - d, r.centery)]
    pygame.draw.polygon(surf, INK, [(x + (x - hx) * 0.35, y + (y - r.centery) * 0.35) for x, y in pts])
    pygame.draw.polygon(surf, WHITE, pts)
    pygame.draw.polygon(surf, GOLD, [(x + (hx - x) * 0.45, y + (r.centery - y) * 0.45) for x, y in pts])


def _bar_fill(w, h):
    def make():
        s = pygame.Surface((w, h), pygame.SRCALPHA)
        g = vgrad(w, h, GOLD_HI, GOLD_LO)
        m = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(m, WHITE, (0, 0, w, h), border_radius=h // 2)
        gg = g.copy()
        gg.blit(m, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        s.blit(gg, (0, 0))
        pygame.draw.line(s, (255, 255, 255, 170), (h // 2, max(1, h // 4)), (w - h // 2, max(1, h // 4)), max(1, px(1)))
        return s
    return _dyn.get_or(("barfill", w, h), make)


# ------------------------------------------------------------ 选手铭牌 ----
# 三档：full（大赛道）/ mid（4 人叠放）/ compact（四宫格等窄赛道：小头像、小字、无触地图，名次盾牌收进卡片右端，
# 倒地/冲线合成一行小标签），另有 strip（追拍小窗，只一行）。
TIERS = {
    "full": dict(P=64, name=23, model=13, sc=30, eff=12, act=(11, 13), minw=262, maxw=380, frac=0.46, pad=7, picto=False),
    "mid": dict(P=52, name=19, model=12, sc=25, eff=11, act=(11, 13), minw=240, maxw=380, frac=0.46, pad=7, picto=False),
    "compact": dict(P=40, name=15, model=10, sc=19, eff=9, act=(9, 11), minw=200, maxw=280, frac=0.46, pad=5, picto=False),
}


def _compact(rect):
    return rect.w < pf(1000)


def tier_of(R, rect):
    t = R._card_tier(rect)
    return "compact" if t != "strip" and _compact(rect) else t


def _card_static(R, rect, lane, i, tier):
    """铭牌静态部分（面板 + 头像徽章 + 名字 + 档位 + 模型）预先合成；返回 (图, 布局信息)。"""
    T = TIERS[tier]
    key = ("card", i, tier, rect.w, lane["name"], lane["model"], lane.get("effort", ""), R.colors[i],
           R.chars[i].key if R.chars[i] is not None else None)

    def make():
        color = R.colors[i]
        pad, P = px(T["pad"]), px(T["P"])
        fs_name, fs_model, fs_sc = px(T["name"]), px(T["model"]), px(T["sc"])
        slant = px(SLANT + (4 if tier != "compact" else 0))
        max_w = int(max(px(T["minw"]), min(px(T["maxw"]), rect.w * T["frac"])))
        tx = pad + P + px(8 if tier == "compact" else 10)
        eff = effort_chip(lane["effort"], px(T["eff"])) if lane.get("effort") else None
        sc_h = ntext("0.00", fs_sc, ow=px(2)).get_height()
        al, av = T["act"]
        sc_w = (ntext("00.00", fs_sc, ow=px(2)).get_width() + ntext("m", int(fs_sc * 0.55), ow=px(1), sh=0).get_width() + px(8)
                + stext("实际", px(al), ow=px(1), sh=0).get_width() + ntext("00.00", px(av), ow=px(1), sh=0).get_width() + px(4))
        right = px(34) if T["picto"] else 0                  # 触地小人图暗槽
        sh_slot = 0
        if tier == "compact" and len(R.lanes) >= 2:          # 名次盾牌收在卡片右端（名字行右侧），不压头像
            sh_slot = int(px(T["P"]) * 0.86 * 0.9) + px(6)
            right = sh_slot
        room = max_w - tx - slant - right
        eff_name = eff if tier != "compact" else None          # 紧凑铭牌：档位标签放到模型那一行，名字整行可用
        eff_model = eff if tier == "compact" else None
        name_img = fit_stext(lane["name"], room - (eff_name.get_width() + px(5) if eff_name else 0), fs_name, max(px(11), fs_name - px(5)),
                             fill=WHITE, kind="cn", ow=px(2) if tier != "compact" else px(1))
        model_img = None
        if lane["model"]:
            model_img = fit_stext(lane["model"], room - (eff_model.get_width() + px(4) if eff_model else 0), fs_model,
                                  max(px(8), fs_model - px(2)), fill=(196, 204, 228), kind="cnr", ow=0, sh=0)
        row1 = max(name_img.get_height(), eff_name.get_height() if eff_name else 0)
        row2 = max(model_img.get_height() if model_img else 0, eff_model.get_height() if eff_model else 0)
        rows = row1 - px(3) + row2 + sc_h - px(3)
        hh = max(P + 2 * pad, rows + 2 * pad)
        mw = (model_img.get_width() if model_img else 0) + (eff_model.get_width() + px(4) if eff_model else 0)
        want = max(name_img.get_width() + (eff_name.get_width() + px(5) if eff_name else 0), mw, sc_w)
        cw = int(max(px(T["minw"]), min(max(max_w, tx + sc_w + right + slant + px(6)), tx + want + right + slant + px(6))))
        S = plate(cw, hh, NAVY_T, NAVY_B, PANEL_A, slant=slant, band=(color, px(3) if tier != "compact" else px(2))).copy()
        blk = pygame.Surface((pad + P + px(8), hh), pygame.SRCALPHA)       # 头像徽章背后：队伍色斜块
        pygame.draw.polygon(blk, (*shade(color, 0.55), 150), [(px(2), px(5)), (pad + P + px(8), px(5)), (pad + P - px(4), hh - px(2)), (px(2), hh - px(2))])
        mask = pygame.Surface(blk.get_size(), pygame.SRCALPHA)
        pygame.draw.polygon(mask, WHITE, _inner(cw, hh, (px(CUT),) * 4, slant, px(OW)))
        blk.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        S.blit(blk, (0, 0))
        S.blit(portrait_or_badge(R, i, P), (pad, (hh - P) // 2))
        y = (hh - rows) // 2
        S.blit(name_img, (tx, y + (row1 - name_img.get_height()) // 2))
        if eff_name:
            S.blit(eff_name, (tx + name_img.get_width() + px(5), y + (row1 - eff_name.get_height()) // 2 + px(1)))
        y += row1 - px(3)
        if row2:
            mx = tx
            if model_img:
                S.blit(model_img, (mx, y + (row2 - model_img.get_height()) // 2))
                mx += model_img.get_width() + px(4)
            if eff_model:
                S.blit(eff_model, (mx, y + (row2 - eff_model.get_height()) // 2))
            y += row2
        info = dict(w=cw, h=hh, tx=tx, score_y=y - px(3), fs_sc=fs_sc, pad=pad, slant=slant, P=P, tier=tier,
                    act=(px(al), px(av)), picto=T["picto"], sh_slot=sh_slot)
        return S, info
    return cached(key, make)


def card_rect(R, rect, lane, i):
    _S, info = _card_static(R, rect, lane, i, tier_of(R, rect))
    return pygame.Rect(rect.x + px(8), rect.y + px(6), info["w"], info["h"])


def _status(R, lane, k):
    """铭牌下方的状态：[("down", cur) / ("fin", 用时) / ("err", 文字)]。cur = 本次倒地以来的水平位移（m）。"""
    out = []
    if lane["down"][k]:
        runs = lane["fx"].runs_until(k)
        out.append(("down", lane["frames"][k]["x"] - runs[-1][2] if runs and k <= runs[-1][1] else 0.0))
    if lane["finish_k"] is not None and k >= lane["finish_k"]:
        out.append(("fin", lane["result"]["finish_time"]))
    if lane["load_error"]:
        out.append(("err", f"载入失败：{lane['load_error'][:40]}"))
    elif lane["result"]["errors"] and k >= lane["result"]["errors"][0][0] * FPS:
        out.append(("err", "act() 报错，沿用上一帧动作"))
    return out


def down_parts(cur):
    """倒地期间位移的写法（与 evaluator 一致：往前的位移被扣掉；往后滑不扣也不加，成绩跟着实际位置走）。
    → (中文前缀或 None, 数字串)：往前 0.12 → (None, "-0.12m")；往后 0.04 → ("后退", "0.04m")。"""
    return (None, f"-{max(0.0, cur):.2f}m") if cur >= -0.005 else ("后退", f"{-cur:.2f}m")


def _bar(kind, label, w, h):
    key = ("sbar", kind, label, w, h)

    def make():
        if kind == "down":
            top, bot, fg = (236, 60, 60), (150, 14, 24), WHITE
        elif kind == "fin":
            top, bot, fg = GOLD_HI, GOLD_LO, INK
        else:
            top, bot, fg = (200, 60, 60), (120, 20, 30), WHITE
        S = plate(w, h, top, bot, 245, slant=px(8), cut=px(3)).copy()
        x = px(6)
        if kind == "down":            # 左端黄黑警示斜纹
            hz = pygame.Surface((h + px(6), h - px(4)), pygame.SRCALPHA)
            hz.fill((250, 200, 40))
            for xx in range(-h, hz.get_width() + h, px(9)):
                pygame.draw.polygon(hz, (24, 24, 28), [(xx, hz.get_height()), (xx + px(4), hz.get_height()), (xx + px(4) + hz.get_height(), 0), (xx + hz.get_height(), 0)])
            S.blit(hz, (px(8), px(2)))
            ic = icon("warn", h - px(6))
            S.blit(ic, (px(8) + (hz.get_width() - ic.get_width()) // 2, px(3)))
            x = px(8) + hz.get_width() + px(6)
        elif kind == "fin":
            ic = icon("flag", h - px(4))
            S.blit(ic, (px(10), px(2)))
            x = px(12) + ic.get_width()
        if label:
            fs = max(px(11), h - px(9))
            img = fit_stext(label, w - x - (px(80) if kind != "err" else px(16)), fs, max(px(9), fs - px(3)), fill=fg, kind="cn",
                            ow=px(1) if fg == WHITE else 0, oc=shade(bot, 0.45), sh=0)
            S.blit(img, (x, (h - img.get_height()) // 2))
            x += img.get_width()
        return S, x
    return cached(key, make)


def _down_value(cur, h, col=(255, 236, 120)):
    """倒地数字（"后退"两个中文字用中文字体，数字用计分字体）。"""
    pre, num = down_parts(cur)
    n = ntext(num, h, col, ow=px(1), oc=(90, 10, 16), sh=0)
    if not pre:
        return n
    p = stext(pre, max(px(9), int(h * 0.8)), col, "cn", ow=px(1), oc=(90, 10, 16), sh=0)
    out = pygame.Surface((p.get_width() + px(3) + n.get_width(), max(p.get_height(), n.get_height())), pygame.SRCALPHA)
    out.blit(p, (0, (out.get_height() - p.get_height()) // 2))
    out.blit(n, (p.get_width() + px(3), (out.get_height() - n.get_height()) // 2))
    return out


def _status_rows(R, surf, x0, by, info, lane, k):
    """普通铭牌：每种状态一条；"已冲线"和别的状态同时出现时缩成短标签并到第一条的右端（只占一行）。返回总高度。"""
    st = _status(R, lane, k)
    if not st:
        return 0
    bh = px(22) if info["tier"] == "full" else px(20)
    bw = info["w"] - info["slant"] // 2
    fin = next((s for s in st if s[0] == "fin"), None)
    rest = [s for s in st if s[0] != "fin"]
    y = by
    if fin and not rest:
        S2, nx = _bar("fin", "已冲线", bw, bh)
        surf.blit(S2, (x0, y))
        n = ntext(f"{fin[1]:.2f}s", bh - px(5), INK, ow=0, sh=0)
        surf.blit(n, (x0 + nx + px(8), y + (bh - n.get_height()) // 2))
        return bh + px(3)
    fw = 0
    if fin:                                        # 短标签：方格旗 + 用时
        n = ntext(f"{fin[1]:.2f}s", bh - px(6), INK, ow=0, sh=0)
        fw = n.get_width() + bh + px(16)
    for j, (kind, val) in enumerate(rest):
        w = bw - (fw + px(3) if j == 0 and fin else 0)
        S2, nx = _bar(kind, ("倒地" if j == 0 and fin else "倒地 · 不计分") if kind == "down" else val, w, bh)   # 同行有冲线标签时缩成"倒地"
        surf.blit(S2, (x0, y))
        if kind == "down":
            nimg = _down_value(val, bh - px(5))
            surf.blit(nimg, (x0 + nx + px(8), y + (bh - nimg.get_height()) // 2))
        if j == 0 and fin:
            S3, nx3 = _bar("fin", "", fw, bh)
            surf.blit(S3, (x0 + w + px(3), y))
            surf.blit(n, (x0 + w + px(3) + nx3 + px(2), y + (bh - n.get_height()) // 2))
        y += bh + px(3)
    return y - by


def _chip_row(R, surf, x0, by, info, lane, k):
    """紧凑铭牌：所有状态合成一行小标签（⚠ 倒地 -0.12m | 🏁 11.55s | 报错）。返回高度。"""
    st = _status(R, lane, k)
    if not st:
        return 0
    h = px(16)
    x = x0
    for kind, val in st:
        if kind == "down":
            v = _down_value(val, h - px(4))
            lab = stext("倒地", h - px(5), WHITE, "cn", ow=px(1), oc=(110, 10, 20), sh=0)
            w = h + lab.get_width() + v.get_width() + px(16)
            surf.blit(plate(w, h, (236, 60, 60), (150, 14, 24), 245, slant=px(6), cut=px(2), shadow=False), (x, by))
            surf.blit(icon("warn", h - px(4)), (x + px(4), by + px(2)))
            surf.blit(lab, (x + h + px(3), by + (h - lab.get_height()) // 2))
            surf.blit(v, (x + h + px(6) + lab.get_width(), by + (h - v.get_height()) // 2))
        elif kind == "fin":
            v = ntext(f"{val:.2f}s", h - px(4), INK, ow=0, sh=0)
            w = h + v.get_width() + px(14)
            surf.blit(plate(w, h, GOLD_HI, GOLD_LO, 255, slant=px(6), cut=px(2), shadow=False), (x, by))
            surf.blit(icon("flag", h - px(3)), (x + px(4), by + px(1)))
            surf.blit(v, (x + h + px(4), by + (h - v.get_height()) // 2))
        else:
            v = fit_stext(val, info["w"] - (x - x0) - px(14), h - px(5), px(8), fill=WHITE, kind="cn", ow=0, sh=0)
            w = v.get_width() + px(14)
            surf.blit(plate(w, h, (200, 60, 60), (120, 20, 30), 245, slant=px(6), cut=px(2), shadow=False), (x, by))
            surf.blit(v, (x + px(6), by + (h - v.get_height()) // 2))
        x += w + px(3)
    return h + px(2)


def _end_extra(R, k):
    """结束画面停留期间（end_t）让冲线横幅、名次箭头这类动画继续走完，而不是冻在最后一帧。"""
    e = getattr(R, "_g_end_t", None)
    return e if e is not None and k >= R.n_frames - 1 else 0.0


def draw_hud(R, surf, rect, lane, i, k, fr, ui):
    """左上角选手铭牌 + 状态条 + 实时名次盾牌 + （可选）关节小面板。"""
    f = lane["frames"][k]
    kf = k + fr
    tier = tier_of(R, rect)
    if tier == "strip":
        _strip(R, surf, rect, lane, i, f["score"])
        return
    S, info = _card_static(R, rect, lane, i, tier)
    x0, y0 = rect.x + px(8), rect.y + px(6)
    down = lane["down"][k]
    if down:                                     # 倒地：整张卡红色脉冲外发光
        g, gp = cached(("cardglow", info["w"], info["h"], info["slant"]), lambda: glowed(
            tint(plate(info["w"], info["h"], slant=info["slant"], shadow=False), RED), RED, px(5 if tier != "compact" else 3), 3))
        g.set_alpha(int(150 + 105 * abs(math.sin(kf / FPS * math.pi * 2.0))))
        surf.blit(g, (x0 - gp, y0 - gp))
    surf.blit(S, (x0, y0))
    if down:
        ring = cached(("redring", info["w"], info["h"], info["slant"]), lambda: _ring_only(info["w"], info["h"], info["slant"]))
        surf.blit(ring, (x0, y0))
    # 成绩（计分数字，过整米弹一下）
    fs = info["fs_sc"]
    ow_sc = px(2) if tier != "compact" else px(1)
    sc0 = sc = ntext(f"{f['score']:.2f}", fs, grad=(GOLD_HI, GOLD_LO), ow=ow_sc)
    j = _last_le(R.g_meter[i], k)
    if j >= 0 and ui.fx_on and k < R.n_frames - 1:
        tp = (kf - R.g_meter[i][j]) / FPS
        if tp < METER_POP:
            u = tp / METER_POP
            s = 1 + 0.28 * (1 - u) ** 2 * math.cos(u * 7)
            sc = pygame.transform.smoothscale(sc, (int(sc.get_width() * s), int(sc.get_height() * s)))
    sx = x0 + info["tx"]
    sy = y0 + info["score_y"]
    base_h = ntext("0.00", fs, ow=px(2)).get_height()
    surf.blit(sc, (sx - (sc.get_width() - sc0.get_width()) // 2, sy + base_h - sc.get_height() + (sc.get_height() - sc0.get_height()) // 2))
    ux = sx + sc0.get_width() + (sc.get_width() - sc0.get_width()) // 2 - px(1)
    unit = ntext("m", int(fs * 0.55), WHITE, ow=px(1), sh=0)
    surf.blit(unit, (ux, sy + base_h - unit.get_height() - px(4)))
    if abs(f["x"] - f["score"]) >= 0.005:        # 实际距离（次要）
        al, av = info["act"]
        lab = stext("实际", al, (255, 150, 140), "cn", ow=px(1), sh=0)
        val = ntext(f"{f['x']:.2f}", av, (255, 170, 160), ow=px(1), sh=0)
        ax = ux + unit.get_width() + px(6)
        ay = sy + base_h - val.get_height() - px(3)
        surf.blit(lab, (ax, ay + (val.get_height() - lab.get_height()) // 2))
        surf.blit(val, (ax + lab.get_width() + px(2), ay))
    if info["picto"]:                            # 触地小人图（右侧暗槽）
        u = pf(4.6) if tier == "full" else pf(4.0)
        pw = int(u * 2.6) + px(8)
        wx = x0 + info["w"] - info["slant"] - pw - px(4)
        well = plate(pw, info["h"] - px(16), (16, 18, 32), (26, 30, 50), 200, ow=px(1), cut=px(3), shadow=False, gloss=False, hi=False)
        surf.blit(well, (wx, y0 + px(8)))
        hud.contact_picto(surf, wx + pw / 2, y0 + (info["h"] - u * 2.4) / 2 - px(1), u, f["contact"])
    by = y0 + info["h"] + px(2)                  # 状态（挂在铭牌下方）
    if tier == "compact":
        _chip_row(R, surf, x0, by, info, lane, k)
    else:
        _status_rows(R, surf, x0, by, info, lane, k)
    # 实时名次盾牌：普通铭牌挂在卡片右侧外沿；紧凑铭牌收在卡片右端（名字行右侧）。都不压头像。名次变化时弹出 + 箭头
    if len(R.lanes) >= 2 and k >= R.g_rank_from:
        rk = R.g_rank[k][i]
        inside = tier == "compact"
        sh_h = int(info["h"] * (0.74 if inside else 0.82))
        img = rank_shield(sh_h, rk)
        j = _last_le(R.g_rank_chg_k[i], k)
        dt = (kf - R.g_rank_chg[i][j][0]) / FPS + _end_extra(R, k) if j >= 0 else 99
        s = 1.0
        if dt < RANK_POP and ui.fx_on:
            s = 1 + 0.55 * math.exp(-dt / 0.09) * math.cos(dt * 22)
        if s != 1.0:
            img = pygame.transform.smoothscale(img, (max(2, int(img.get_width() * s)), max(2, int(img.get_height() * s))))
        if inside:
            cx = x0 + info["w"] - info["slant"] - info["sh_slot"] // 2 + px(2)
        else:
            cx = x0 + info["w"] + px(6) + int(sh_h * 0.43)
        cy = y0 + info["h"] // 2
        surf.blit(img, (cx - img.get_width() // 2, cy - img.get_height() // 2))
        if dt < RANK_ARROW:
            up = R.g_rank_chg[i][j][1] < 0
            ar = icon("up" if up else "down", px(14) if not inside else px(11))
            if dt > RANK_ARROW - 0.3:
                ar = ar.copy()
                ar.set_alpha(int(255 * (RANK_ARROW - dt) / 0.3))
            surf.blit(ar, (cx + int(sh_h * 0.36), cy - ar.get_height() // 2 + (-px(8) if up else px(8))))
    if ui.show_wave and rect.w >= pf(600) and rect.h >= pf(140):
        draw_wave(R, surf, rect, lane, k, fr, ui)


def _ring_only(w, h, slant):
    """红色描边环（倒地时盖在铭牌外框上）。"""
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    outer = _shape(w, h, (px(CUT),) * 4, slant)
    inner = _inner(w, h, (px(CUT),) * 4, slant, px(3))
    pygame.draw.polygon(s, (*RED, 255), outer)
    pygame.draw.polygon(s, (0, 0, 0, 0), inner)
    return s


def _strip(R, surf, rect, lane, i, score):
    """极小赛道（追拍/焦点小窗）：底部一行迷你铭牌。"""
    fs = max(px(12), min(px(15), int(rect.h * 0.12)))
    name = fit_stext(lane["name"], int(rect.w * 0.45), fs, px(11), fill=WHITE, kind="cn", ow=px(1))
    sc = ntext(f"{score:.2f}m", fs + px(1), grad=(GOLD_HI, GOLD_LO), ow=px(1))
    d = max(name.get_height(), sc.get_height()) + px(4)
    w = d + px(8) + name.get_width() + px(10) + sc.get_width() + px(18)
    x, y = rect.x + px(8), rect.bottom - d - px(8)
    surf.blit(plate(w, d, slant=px(8), cut=px(3), band=(R.colors[i], px(2))), (x, y))
    surf.blit(portrait_or_badge(R, i, d - px(4), ring_w=px(1)), (x + px(3), y + px(1)))
    surf.blit(name, (x + d + px(6), y + (d - name.get_height()) // 2))
    surf.blit(sc, (x + d + px(16) + name.get_width(), y + (d - sc.get_height()) // 2))


def wave_rect(R, rect, ui):
    """游戏版关节小面板的位置（隐藏时 None）。"""
    if not ui.show_wave or rect.w < pf(600) or rect.h < pf(140):
        return None
    ww = px(176) if rect.w >= pf(1000) else px(140)
    wh = max(px(40), min(px(60), int(rect.h * 0.2))) + px(13)
    return pygame.Rect(rect.right - ww - px(10), rect.y + px(8), ww, wh)


def hud_obstacles(R, rect, lane, i, ui):
    """弹窗/伤害数字选位置时要避开的 HUD 区域（铭牌 + 状态条 + 名次盾牌 + 关节面板）。"""
    c = card_rect(R, rect, lane, i)
    if tier_of(R, rect) == "compact":
        out = [pygame.Rect(c.x, c.y, c.w, c.h + px(20)).inflate(px(8), px(8))]
    else:
        out = [pygame.Rect(c.x, c.y, c.w + int(c.h * 0.9) + px(10), c.h + px(48)).inflate(px(8), px(8))]
    wr = wave_rect(R, rect, ui)
    if wr:
        out.append(wr.inflate(px(8), px(8)))
    return out


def draw_wave(R, surf, rect, lane, k, fr, ui):
    wr = wave_rect(R, rect, ui)
    js = range(6) if ui.all_joints else (0, 2, 4, 5)
    surf.blit(plate(wr.w, wr.h, (30, 36, 62), (14, 16, 30), 205, cut=px(4), shadow=False), wr.topleft)
    lh = px(13)
    gx, gy, gw, gh = wr.x + px(5), wr.y + px(4), wr.w - px(10), wr.h - lh - px(6)
    pygame.draw.line(surf, (60, 68, 96), (gx, gy + gh // 2), (gx + gw, gy + gh // 2), px(1))
    n = int(V.WAVE_SECONDS * FPS)
    frames = lane["frames"]
    ks = list(range(k, max(0, k - n) - 1, -2))
    kf = k + fr
    for j in js:
        pts = [(gx + gw - (kf - kk) * gw / n, gy + gh / 2 - frames[kk]["joints"][j] * (gh / 2 - px(2))) for kk in ks]
        if fr:
            a = frames[k]["joints"][j]
            pts.insert(0, (gx + gw, gy + gh / 2 - (a + (frames[k + 1]["joints"][j] - a) * fr) * (gh / 2 - px(2))))
        if len(pts) > 1:
            pygame.draw.lines(surf, V.JOINT_COLORS[j], False, pts, px(2))
    lx, ly = wr.x + px(6), wr.bottom - lh // 2 - px(3)
    for j in js:
        img = fx.text(V.JOINT_SHORT[j], px(9), mix(V.JOINT_COLORS[j], WHITE, 0.25), True)
        pygame.draw.circle(surf, V.JOINT_COLORS[j], (lx + px(3), ly), px(3))
        surf.blit(img, (lx + px(8), ly - img.get_height() // 2))
        lx += img.get_width() + px(13)
    if ui.interactive:
        tail = fx.text("J", px(9), (140, 150, 176), True)
        surf.blit(tail, (wr.right - tail.get_width() - px(6), ly - tail.get_height() // 2))


def cycle_wave(ui):
    """J 键（游戏版）：关 → 4 条 → 全部 6 条 → 关。"""
    if not ui.show_wave:
        ui.show_wave, ui.all_joints = True, False
    elif not ui.all_joints:
        ui.all_joints = True
    else:
        ui.show_wave, ui.all_joints = False, False


# ------------------------------------------------------------ 头顶：气泡 / 扣分 ----
ALARM = ("back", "face", "faceslide", "split", "unbalanced", "fly")


def bubble_sprite(label, fs, color, tail_dir="down"):
    """漫画对话框：普通 = 白底圆角框 + 粗描边 + 队伍色图标圆；摔倒类 = 黄色爆炸框 + 红字。"""
    def make():
        name, ic = fx.bubble_label(label)
        alarm = label in ALARM
        ow = max(px(2), fs // 7)
        txt = stext(name, fs, (190, 24, 24) if alarm else INK, "cn", ow=px(1) if alarm else 0, oc=WHITE, sh=0)
        isz = int(fs * 1.2) if ic else 0
        pad = max(px(6), fs // 3)
        bw = txt.get_width() + 2 * pad + (isz + px(4) if ic else 0)
        bh = max(txt.get_height(), isz) + pad
        tail = max(px(6), fs // 2)
        spike = int(fs * 0.28) if alarm else 0
        m = ow + spike + px(2)
        if tail_dir == "down":
            S = pygame.Surface((bw + 2 * m, bh + 2 * m + tail), pygame.SRCALPHA)
            r = pygame.Rect(m, m, bw, bh)
            tip, base_a, base_b = (r.centerx - px(2), r.bottom + tail + spike), (r.centerx - tail, r.bottom - px(2)), (r.centerx + tail * 0.4, r.bottom - px(2))
        elif tail_dir == "left":
            S = pygame.Surface((bw + 2 * m + tail, bh + 2 * m), pygame.SRCALPHA)
            r = pygame.Rect(m + tail, m, bw, bh)
            tip, base_a, base_b = (r.x - tail - spike, r.centery + px(2)), (r.x + px(2), r.centery - tail * 0.7), (r.x + px(2), r.centery + tail * 0.5)
        else:
            S = pygame.Surface((bw + 2 * m + tail, bh + 2 * m), pygame.SRCALPHA)
            r = pygame.Rect(m, m, bw, bh)
            tip, base_a, base_b = (r.right + tail + spike, r.centery + px(2)), (r.right - px(2), r.centery - tail * 0.7), (r.right - px(2), r.centery + tail * 0.5)
        fill = (255, 214, 64) if alarm else WHITE
        if alarm:
            cx, cy = r.center
            rx, ry = r.w / 2 + spike * 0.6, r.h / 2 + spike * 0.6
            nsp = max(12, int((rx + ry) / max(1, fs * 0.45)))
            pts = []
            for j in range(2 * nsp):
                a = math.pi * j / nsp
                k_ = 1.0 if j % 2 == 0 else 0.78
                pts.append((cx + math.cos(a) * (rx + spike * 0.5) * k_, cy + math.sin(a) * (ry + spike * 0.5) * k_))
            pygame.draw.polygon(S, INK, [tip, base_a, base_b])
            pygame.draw.polygon(S, INK, pts)
            ins = [(cx + (x - cx) * (1 - ow / rx * 1.3), cy + (y - cy) * (1 - ow / ry * 1.3)) for x, y in pts]
            tri = [(tip[0] + (cx - tip[0]) * 0.12, tip[1] + (cy - tip[1]) * 0.12), base_a, base_b]
            pygame.draw.polygon(S, fill, tri)
            pygame.draw.polygon(S, fill, ins)
            pygame.draw.ellipse(S, (255, 240, 150), r.inflate(-r.w * 0.3, -r.h * 0.45).move(0, -r.h * 0.12))
        else:
            pygame.draw.polygon(S, INK, [(tip[0], tip[1] + (ow if tail_dir == "down" else 0)), base_a, base_b])
            pygame.draw.rect(S, INK, r.inflate(2 * ow, 2 * ow), border_radius=bh // 2 + ow)
            pygame.draw.rect(S, fill, r, border_radius=bh // 2)
            tri = [(tip[0] + (r.centerx - tip[0]) * 0.18, tip[1] + (r.centery - tip[1]) * 0.18), base_a, base_b]
            pygame.draw.polygon(S, fill, tri)
            pygame.draw.line(S, (226, 230, 240), (r.x + bh // 2, r.bottom - px(2)), (r.right - bh // 2, r.bottom - px(2)), px(2))
        x = r.x + pad
        if ic:
            pygame.draw.circle(S, INK, (x + isz / 2, r.centery), isz / 2 + px(1))
            pygame.draw.circle(S, color if not alarm else RED, (x + isz / 2, r.centery), isz / 2 - px(1))
            fx.draw_icon(S, ic, x + isz // 2, r.centery, int(isz * 0.86), WHITE)
            x += isz + px(4)
        S.blit(txt, (x, r.centery - txt.get_height() // 2))
        return S
    return cached(("gbubble", label, fs, color, tail_dir), make)


def loss_sprite(amount, fs):
    """伤害数字：红色渐变大号 "-0.42m" + 下方小标签"倒地不计分"。"""
    s = f"{-amount:+.2f}m"

    def make():
        num = ntext(s, int(fs * 1.5), grad=(RED_HI, RED_LO), ow=px(2), oc=INK)
        cap = chip("倒地不计分", max(px(9), int(fs * 0.72)), (190, 26, 36), kind="cn")
        w = max(num.get_width(), cap.get_width())
        S = pygame.Surface((w, num.get_height() + cap.get_height() - px(4)), pygame.SRCALPHA)
        S.blit(cap, ((w - cap.get_width()) // 2, num.get_height() - px(4)))
        S.blit(num, ((w - num.get_width()) // 2, 0))
        return S
    return _dyn.get_or(("loss", s, fs), make)


def draw_overhead(R, v, surf, lane, k, fr, head, color, ui):
    """Renderer._draw_overhead 的游戏版：同样的排布逻辑，换成游戏气泡 + 伤害数字（弹起、淡出）。"""
    rect, frames, lfx = v.rect, lane["frames"], lane["fx"]
    kf = k + fr
    c, Rr = head
    fs = max(px(11), min(px(17), int(v.scale * 0.11)))
    p2, p3, p4 = px(2), px(3), px(4)
    stack = []
    runs = lfx.runs_until(k)
    j = lfx.bubble_at[k]
    if j >= 0:
        k0, k1, key = lfx.bubble_events[j]
        bfs = max(px(12), min(px(19), int(v.scale * 0.13)))
        t, t_end = (kf - k0) / FPS, (k1 - kf) / FPS
        s, alpha = 1.0, 255
        if ui.fx_on and k0 > 0 and t < 0.3:
            s = 0.55 + 0.6 * t / 0.12 if t < 0.12 else 1.15 - 0.15 * (t - 0.12) / 0.18
        elif ui.fx_on and lfx.bubble_mode == "few" and k1 < R.n_frames - 1 and t_end < 0.25:
            s, alpha = 0.7 + 0.3 * max(0.0, t_end) / 0.25, int(255 * max(0.0, t_end) / 0.25)
        stack.append((key, bfs, s, alpha))
    gap = p3
    sizes = [(bubble_sprite(it[0], it[1], color) if isinstance(it, tuple) else it).get_size() for it in stack]
    tot = sum(h + gap for _w, h in sizes)
    side = None
    if c[1] - Rr - p3 - tot < rect.y + p2:
        maxw = max((w for w, _h in sizes), default=0) + px(8)
        y = max(rect.y + p2, min(rect.bottom - tot - p2, c[1] - tot / 2))
        right = pygame.Rect(int(c[0] + Rr + p4), int(y), maxw, int(tot))
        wave = wave_rect(R, rect, ui) if ui.show_hud else None
        side = "right"
        if right.right > rect.right - p2 or (wave and right.colliderect(wave)):
            side = "left"
    else:
        y = c[1] - Rr - p3
    for it in (reversed(stack) if side else stack):
        if isinstance(it, tuple):
            key, bfs, s, alpha = it
            img = bubble_sprite(key, bfs, color, {"right": "left", "left": "right"}.get(side, "down"))
            full_w, full_h = img.get_size()
            if s != 1.0:
                img = pygame.transform.smoothscale(img, (max(4, int(full_w * s)), max(4, int(full_h * s))))
            if alpha < 255:
                img = img.copy()
                img.set_alpha(alpha)
        else:
            img = it
            full_w, full_h = img.get_size()
        if side == "right":
            bx = min(rect.right - full_w - p2, c[0] + Rr + p4)
            surf.blit(img, (bx, y + (full_h - img.get_height()) / 2))
            y += full_h + gap
        elif side == "left":
            bx = max(rect.x + p2, c[0] - Rr - p4 - full_w)
            surf.blit(img, (bx + full_w - img.get_width(), y + (full_h - img.get_height()) / 2))
            y += full_h + gap
        else:
            bx = max(rect.x + p2, min(rect.right - img.get_width() - p2, c[0] - img.get_width() / 2))
            surf.blit(img, (bx, y - full_h + (full_h - img.get_height())))
            y -= full_h + gap
    win = int(FLOAT_SECONDS * FPS)
    for ri, (k0, k1, xb, _loss) in enumerate(lfx.down_runs):      # 倒地瞬间：扣分像伤害数字一样弹出，0.8 秒内跟着实时扣分滚动、上飘、淡出
        if k0 > k:
            break
        t = (kf - k0) / FPS
        if k0 == 0 or t >= FLOAT_SECONDS:
            continue
        peak = max(frames[j]["x"] - xb for j in range(k0, min(k1, k0 + win) + 1))
        if peak < 0.05:                                            # 这 0.8 秒里几乎没往前滑：不飘（卡片红条照样显示）
            continue
        img = loss_sprite(max(0.0, frames[min(k, k1)]["x"] - xb), fs)
        size = loss_sprite(peak, fs).get_size()
        kind = _float_place(R, v, lane, ri, k0, size, ui)
        pose = lane["poses"][k] if fr == 0 else V.lerp_pose(lane["poses"][k], lane["poses"][k + 1], fr)
        x_c = pose[0][0] / PPM
        gy = v.w2s(x_c, physics.terrain_height(x_c, R.track))[1]
        cx, cy = _float_center(kind, R._runner_box(v, pose, head), head, size[0], size[1], gy)
        sc = 1.6 - 0.6 * _ease_back(t / 0.16) if ui.fx_on and t < 0.16 else 1.0
        im = pygame.transform.smoothscale(img, (max(2, int(img.get_width() * sc)), max(2, int(img.get_height() * sc)))) if sc != 1.0 else img.copy()
        im.set_alpha(int(255 * (1 - max(0.0, (t - 0.5) / 0.3)) ** 1.3))
        cy -= px(26) * _ease(t / FLOAT_SECONDS)
        r = pygame.Rect(0, 0, im.get_width(), im.get_height())
        r.center = (int(cx), int(cy))
        surf.blit(im, r.clamp(rect.inflate(-p4, -p4)) if r.w < rect.w and r.h < rect.h else r)


def _popup_rect_at(R, v, lane, i, k, ui):
    """Q 版弹窗在第 k 帧（整数帧、无缩放）的矩形；与 Renderer._draw_popup 的定位一致（共用同一个位置缓存）。没有则 None。"""
    ch = R.chars[i]
    if ch is None:
        return None
    j = lane["popup_at"][k]
    if j < 0:
        return None
    k0, _k1, name, expr = lane["popups"][j]
    rect = v.rect
    hq = max(px(44), min(px(170), int(rect.h * 0.47)))
    img = ch.q_scaled(expr, hq, fx.POPUP_RULES[name]["fallback"], ow=px(2 if hq >= pf(80) else 1))
    if img is None:
        return None
    key = ((j, i), rect.w, rect.h, ui.show_hud)
    place = R._popup_side.get(key)
    if place is None:
        cx0 = lane["cam"][k0]
        v0 = V.View(rect, v.scale, cx0, 0.5 * physics.terrain_height(cx0, R.track))
        p0 = lane["poses"][k0]
        place = R._popup_side[key] = R._popup_place(v0, lane, i, p0, R._avatar_head_at(v0, p0, i), img.get_width(), img.get_height(), ui)
    kind, (dx, dy) = place
    pose = lane["poses"][k]
    (hx, _hy), _hr = R._avatar_head_at(v, pose, i)
    x_c = pose[0][0] / PPM
    gy = v.w2s(x_c, physics.terrain_height(x_c, R.track))[1]
    w, h = img.get_size()
    full = R._popup_rect(kind, hx + dx, gy + dy, w, h)
    p8 = px(8)
    return full.clamp(rect.inflate(-p8, -p8)) if w < rect.w - p8 and h < rect.h - p8 else full


def _float_center(kind, body, head, w, h, gy):
    """伤害数字中心：0 身体前方（头的高度）/ 1 身体后方 / 2 头顶气泡区再往上 / 3 贴地略靠前。"""
    (hx, hy), hr = head
    if kind == 0:
        return body.right + w / 2 + px(14), hy
    if kind == 1:
        return body.left - w / 2 - px(14), hy
    if kind == 2:
        return hx, hy - hr - px(70) - h / 2
    return body.centerx + w * 0.6, gy - h / 2 - px(6)


def _float_place(R, v, lane, ri, k0, size, ui):
    """伤害数字放哪一侧：在这段倒地开头的 0.8 秒里取 3 个采样帧，算每个候选位置与小人、头顶气泡、Q 版弹窗、HUD 的重叠，
    挑总重叠最小的（缓存）。之后每帧按当前的头/身体位置摆（跟着人走）。"""
    i = next(j for j, L in enumerate(R.lanes) if L is lane)
    rect = v.rect
    key = (i, ri, rect.w, rect.h, ui.show_hud)
    got = R._g_float.get(key)
    if got is not None:
        return got
    w, h = size
    win = int(FLOAT_SECONDS * FPS)
    inner = rect.inflate(-px(8), -px(8))
    hud_obst = hud_obstacles(R, rect, lane, i, ui) if ui.show_hud and tier_of(R, rect) != "strip" else []
    costs = [j_ * pf(pf(30)) for j_ in range(4)]           # 偏好顺序：前方 > 后方 > 头顶 > 贴地
    for ke in sorted({min(R.n_frames - 1, k0 + d) for d in (4, win // 2, win - 4)}):
        cx = lane["cam"][ke]
        ve = V.View(rect, v.scale, cx, 0.5 * physics.terrain_height(cx, R.track))
        pose = lane["poses"][ke]
        if R.chars[i] is not None and R.chars[i].head is not None:
            head = R._avatar_head_at(ve, pose, i)
        else:
            ly = physics.TORSO_H / 2 + physics.HEAD_R
            px_, py_, a = pose[0]
            head = (ve.w2s((px_ - ly * math.sin(a)) / PPM, (py_ + ly * math.cos(a)) / PPM), physics.HEAD_R * ve.scale / PPM)
        (hx, hy), hr = head
        body = R._runner_box(ve, pose, head)
        obst = [body, pygame.Rect(int(hx - hr * 2.4), int(hy - hr - px(64)), int(hr * 4.8), px(64))] + hud_obst
        jb = lane["fx"].bubble_at[ke]
        if jb >= 0:                                # 这时真有气泡：按气泡实际尺寸再加一块
            bw_, bh_ = bubble_sprite(lane["fx"].bubble_events[jb][2], max(px(12), min(px(19), int(ve.scale * 0.13))), R.colors[i]).get_size()
            obst.append(pygame.Rect(int(hx - bw_ / 2 - px(12)), int(hy - hr - bh_ - px(8)), int(bw_ + px(24)), int(bh_ + px(10))))
        pr = _popup_rect_at(R, ve, lane, i, ke, ui)
        if pr is not None:
            obst.append(pr.inflate(px(6), px(6)))
        x_c = pose[0][0] / PPM
        gy = ve.w2s(x_c, physics.terrain_height(x_c, R.track))[1]
        for kind in range(4):
            qx, qy = _float_center(kind, body, head, w, h, gy)
            r = pygame.Rect(0, 0, int(w), int(h) + px(26))       # 连同上飘的距离一起算
            r.midbottom = (int(qx), int(qy + h / 2))
            rc = r.clamp(inner) if r.w < inner.w and r.h < inner.h else r
            costs[kind] += sum(V._overlap(rc, o) for o in obst) + 2 * (abs(rc.x - r.x) + abs(rc.y - r.y)) * h
    got = R._g_float[key] = min(range(4), key=lambda j_: costs[j_])
    return got


# ------------------------------------------------------------ 冲线横幅 ----
def _finish_band(R, i, lane, bw, bh):
    key = ("band", i, bw, bh, lane["result"]["finish_time"])

    def make():
        alt = ui_img("banner_finish", (bw, bh))
        sl = int(bh * 0.45)
        color = R.colors[i]
        if alt is not None:
            S = pygame.Surface((bw, bh), pygame.SRCALPHA)
            S.blit(pygame.transform.smoothscale(alt, (bw, bh)), (0, 0))
        else:
            S = plate(bw, bh, GOLD_HI, GOLD_LO, 255, ow=px(3), slant=sl, cut=0, shadow=True).copy()
            stripe = pygame.Surface(S.get_size(), pygame.SRCALPHA)
            ins = _inner(bw, bh, (0, 0, 0, 0), sl, px(3))
            m = pygame.Surface(S.get_size(), pygame.SRCALPHA)
            pygame.draw.polygon(m, WHITE, ins)
            for yy in (px(3), bh - px(3) - max(px(4), bh // 12)):
                pygame.draw.rect(stripe, (*color, 255), (0, yy, bw, max(px(4), bh // 12)))
            cell = max(px(6), bh // 7)          # 两端方格旗纹（跟横幅一起斜切）
            for ux in (px(6), bw - sl - px(6) - cell * 3):
                for cx in range(3):
                    for cy in range(bh // cell + 1):
                        if (cx + cy) % 2 == 0:
                            y0_, y1_ = cy * cell, min(bh, (cy + 1) * cell)
                            xa, xb = ux + cx * cell, ux + (cx + 1) * cell
                            pygame.draw.polygon(stripe, (24, 24, 30, 230), [(xa + sl * (1 - y0_ / bh), y0_), (xb + sl * (1 - y0_ / bh), y0_),
                                                                            (xb + sl * (1 - y1_ / bh), y1_), (xa + sl * (1 - y1_ / bh), y1_)])
            stripe.blit(m, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
            S.blit(stripe, (0, 0))
        big = stext("FINISH!", int(bh * 0.6), WHITE, "num", ow=max(px(3), bh // 16), oc=(110, 30, 0), sh=px(3))
        sub = stext("冲线！", int(bh * 0.3), WHITE, "disp", ow=px(2), oc=INK)
        tm = ntext(f"{lane['result']['finish_time']:.2f}s", int(bh * 0.32), WHITE, ow=px(2), oc=INK)
        name = fit_stext(lane["name"], int(bw * 0.2), int(bh * 0.22), px(11), fill=WHITE, kind="cn", ow=px(2), oc=shade(color, 0.4))
        d = int(bh * 0.62)
        port = portrait_or_badge(R, i, d)
        right_w = max(sub.get_width() + tm.get_width() + px(6), name.get_width())
        total = port.get_width() + px(10) + big.get_width() + px(14) + right_w
        x = (bw - total) // 2
        S.blit(port, (x, (bh - port.get_height()) // 2))
        x += port.get_width() + px(10)
        S.blit(big, (x, (bh - big.get_height()) // 2 - px(1)))
        x += big.get_width() + px(14)
        y = bh // 2 - sub.get_height() + px(4)
        S.blit(sub, (x, y))
        S.blit(tm, (x + sub.get_width() + px(6), y + sub.get_height() - tm.get_height()))
        S.blit(name, (x, bh // 2 + px(4)))
        return S
    return cached(key, make)


def draw_finish(R, surf, rect, lane, i, k, fr):
    """冲线白闪（赛道内，HUD 之前画）。横幅在 draw_banner 里最后画。"""
    fk = lane["finish_k"]
    if fk is None or not (fk <= k < fk + 150):
        return
    t = (k + fr - fk) / FPS + _end_extra(R, k)
    if t < 0.35:
        a = int(140 * (1 - t / 0.35)) // 10 * 10
        if a > 0:
            surf.blit(fx.panel(rect.w, rect.h, (255, 255, 255), a, 0), rect.topleft)


def _tall_lane(R, rect, ui):
    """单道 / 追拍的大赛道：横幅按小人位置避让。多道分组画面（G1~G4、top4）不走这里，保持原样。"""
    return (ui.follow or len(getattr(R, "last_layout", ()) or ()) == 1) and rect.h >= pf(300)


def _tall_banner_slot(R, rect, lane, i, fk, left, ui):
    """横幅显示期间（冲线后 150 帧）逐帧算小人（含头像）的屏幕包围盒，取最高点；
    横幅放在赛道顶部（关节面板下方）到小人头顶之间的空处，整段固定一个高度，不跟着小人跳。
    返回 (宽, 高, 中心 y)；按 (赛道, 冲线帧, 赛道尺寸) 缓存。"""
    cache = R.__dict__.setdefault("_g_banner_slot", {})
    key = (i, fk, rect.x, rect.y, rect.w, rect.h, left, ui.show_hud, ui.show_wave)
    if key in cache:
        return cache[key]
    scale = V._scale_for(rect)
    poses, cams = lane["poses"], lane["cam"]
    body_top = rect.bottom
    for kk in range(fk, min(fk + 150, len(poses) - 1) + 1):
        cx = cams[kk]
        v = V.View(rect, scale, cx, 0.5 * physics.terrain_height(cx, R.track))
        head = R._avatar_head_at(v, poses[kk], i)
        body_top = min(body_top, R._runner_box(v, poses[kk], head).top)
    body_top -= px(14)                  # 镜头抖动 + 留白
    y_min = rect.y + px(12)
    wr = wave_rect(R, rect, ui)
    if wr is not None:
        y_min = max(y_min, wr.bottom + px(8))
    bw = int(min(rect.w * 0.6, px(760), rect.right - left - px(12)))
    bh = int(max(px(44), min(px(84), bw / 7.0)))
    room = body_top - y_min
    if room < bh:                       # 空处不够高：横幅压扁到能放下（最低 44）
        bh = int(max(px(44), room))
    y = y_min + max(0, room - bh) * 0.35
    cache[key] = got = (bw, bh, y + bh / 2)
    return got


def draw_banner(R, surf, rect, lane, i, k, fr, ui):
    """"FINISH! 冲线！xx.xxs" 斜切横幅从右向左滑过本赛道（滑入 → 缓慢漂移 → 加速滑出）。
    停留时放在铭牌 + 名次盾牌右边的空处（宽度按空处收），所以不会盖住选手卡；整帧最后画，选手卡也盖不住它。"""
    fk = lane["finish_k"]
    if fk is None or not (fk <= k < fk + 150):
        return
    t = (k + fr - fk) / FPS + _end_extra(R, k)
    t_out = BANNER_IN + BANNER_HOLD
    if t >= t_out + BANNER_OUT:
        return
    left = rect.x + px(10)
    if ui.show_hud and tier_of(R, rect) != "strip":
        left = hud_obstacles(R, rect, lane, i, ui)[0].right + px(6)
    slot = _tall_banner_slot(R, rect, lane, i, fk, left, ui) if _tall_lane(R, rect, ui) else None
    if slot is not None:                # 单道 / 追拍：窄横幅，固定在小人头顶以上的空处
        bw, bh, cy = slot
    else:
        bw = int(min(rect.w * 0.82, px(980), rect.right - left - px(12)))
        bh = int(max(px(40), min(px(100), rect.h * 0.34))) if not _compact(rect) else int(max(px(36), min(px(76), rect.h * 0.26)))
        bh = int(max(px(30), min(bh, bw / 6.2)))
        cy = rect.y + rect.h * 0.42
    band = _finish_band(R, i, lane, bw, bh)
    cx0, drift = (left + rect.right) / 2, min(px(26), (rect.right - left - bw) / 2)
    if t < BANNER_IN:
        cx = rect.right + bw / 2 + (cx0 + drift - rect.right - bw / 2) * _ease(t / BANNER_IN)
    elif t < t_out:
        cx = cx0 + drift - 2 * drift * (t - BANNER_IN) / BANNER_HOLD
    else:
        cx = cx0 - drift + (rect.x - bw / 2 - px(40) - cx0 + drift) * _ease_in((t - t_out) / BANNER_OUT)
    x, y = int(cx - bw / 2), int(cy - bh / 2)
    for j in range(5):                  # 横幅后面的速度线
        ly = y + bh * (0.15 + 0.17 * j)
        ln = bw * (0.18 + 0.07 * ((j * 7) % 3))
        pygame.draw.line(surf, (255, 255, 255), (x + bw - bh * 0.2 + px(6), ly), (x + bw - bh * 0.2 + px(6) + ln, ly), px(3) if j % 2 else px(2))
    surf.blit(band, (x, y))


# ------------------------------------------------------------ 倒计时 ------
def draw_countdown(R, surf, t, ui):
    """起跑倒计时 3-2-1-GO（盖在回放第 0 帧上，不改变比赛帧）：红色起跑灯 + 大号计分数字弹出 + 冲击波圈。"""
    W, H = V.W, V.H
    key = (ui.layout, ui.follow, ui.focus, ui.show_hud, ui.fx_on, ui.show_wave, ui.all_joints, W)
    if R._g_cd_base is None or R._g_cd_base[0] != key:
        base = pygame.Surface((W, H))
        ui0 = ui.end_t
        ui.end_t = None
        R.draw(base, 0, ui)
        ui.end_t = ui0
        dim = pygame.Surface((W, H), pygame.SRCALPHA)
        dim.fill((6, 8, 18, 96))
        base.blit(dim, (0, 0))
        R._g_cd_base = (key, base)
    surf.blit(R._g_cd_base[1], (0, 0))
    step = min(3, int(t / COUNT_STEP))
    u = t - step * COUNT_STEP
    go = step == 3
    cx, cy = W // 2, int(V.TOP + (H - V.TOP) * 0.46)
    # 起跑灯：三盏，3/2/1 依次点红，GO 全绿
    lr, gap = px(26), px(70)
    lamp_y = cy - px(190)
    surf.blit(plate(gap * 3 + px(20), lr * 2 + px(24), (40, 44, 60), (16, 18, 28), 240, cut=px(10)), (cx - gap * 3 // 2 - px(10), lamp_y - lr - px(12)))
    for j in range(3):
        lx = cx + (j - 1) * gap
        on = go or j <= step
        col = GO_LO if go else (RED if on else (70, 30, 34))
        pygame.draw.circle(surf, INK, (lx, lamp_y), lr + px(3))
        pygame.draw.circle(surf, col, (lx, lamp_y), lr)
        if on:
            pygame.draw.circle(surf, mix(col, WHITE, 0.55), (lx - lr * 0.3, lamp_y - lr * 0.3), lr * 0.32)
    # 冲击波圈
    if u < 0.55:
        rr = int(px(110) + px(360) * _ease(u / 0.55))
        a = int(210 * (1 - u / 0.55))
        ring = pygame.Surface((2 * rr + 8, 2 * rr + 8), pygame.SRCALPHA)
        pygame.draw.circle(ring, (*(GO_HI if go else GOLD), a), (rr + 4, rr + 4), rr, px(7))
        surf.blit(ring, (cx - rr - 4, cy - rr - 4))
    if go:                                # GO：旋转放射光
        rays = cached(("cd_rays", W), lambda: _rays(px(620)))
        a = (t - 3 * COUNT_STEP) * 40
        img = pygame.transform.rotate(rays, a)
        img.set_alpha(int(200 * (1 - _ease_in(u / COUNT_STEP))))
        surf.blit(img, (cx - img.get_width() // 2, cy - img.get_height() // 2))
    label = "GO!" if go else str(3 - step)
    size = px(150) if go else px(190)
    grad = (GO_HI, GO_LO) if go else (GOLD_HI, GOLD_LO)
    big, gp = cached(("cd", label, size), lambda: glowed(stext(label, size, WHITE, "num", ow=px(7), oc=INK, grad=grad, sh=px(6)),
                                                          (255, 230, 120) if not go else (160, 255, 140), px(10), 2))
    s = 1.0
    if u < 0.2:
        s = 1.8 - 0.8 * _ease_back(u / 0.2, 2.2)
    alpha = 255
    if u > COUNT_STEP - 0.14:
        e = (u - (COUNT_STEP - 0.14)) / 0.14
        s *= 1 - 0.25 * e
        alpha = int(255 * (1 - e))
    img = big if s == 1.0 else pygame.transform.smoothscale(big, (max(2, int(big.get_width() * s)), max(2, int(big.get_height() * s))))
    if alpha < 255:
        img = img.copy()
        img.set_alpha(alpha)
    surf.blit(img, (cx - img.get_width() // 2, cy - img.get_height() // 2))
    sub = stext("开跑！" if go else "预备", px(34), WHITE, "disp", ow=px(3), oc=INK)
    surf.blit(sub, (cx - sub.get_width() // 2, cy + px(118)))


def _rays(r):
    s = pygame.Surface((2 * r, 2 * r), pygame.SRCALPHA)
    for j in range(16):
        a = j * math.pi / 8
        pygame.draw.polygon(s, (255, 250, 200, 70 if j % 2 else 40),
                            [(r, r), (r + math.cos(a - 0.09) * r, r + math.sin(a - 0.09) * r), (r + math.cos(a + 0.09) * r, r + math.sin(a + 0.09) * r)])
    return s


# ------------------------------------------------------------ 结算界面 ----
def _board_bg(W, H, TOP, PW):
    def make():
        s = vgrad(PW, H - TOP, (30, 36, 66), (10, 12, 24)).copy()
        for x in range(-H, PW, px(26)):
            pygame.draw.line(s, (34, 40, 74), (x, H - TOP), (x + (H - TOP), 0), px(8))
        pygame.draw.rect(s, INK, (0, 0, px(5), H - TOP))
        pygame.draw.rect(s, GOLD, (px(1), 0, px(3), H - TOP))
        return s
    return cached(("boardbg", W, H, TOP, PW), make)


def draw_board(R, surf, end_t):
    """比赛结束：右侧"结算界面"——RESULT 标题、逐行滑入、名次奖牌、头像框、成绩/冲线用时分栏计数、评级徽章盖章。"""
    W, H, TOP, PW = V.W, V.H, V.TOP, V.PANEL_W
    x0 = W - PW
    surf.blit(_board_bg(W, H, TOP, PW), (x0, TOP))
    e = _ease(end_t / 0.35)
    big = stext("RESULT", px(34), WHITE, "num", grad=(GOLD_HI, GOLD_LO), ow=px(3), oc=INK, sh=px(3))
    surf.blit(big, (x0 + PW // 2 - big.get_width() // 2 + int((1 - e) * px(60)), TOP + px(6)))
    rib_t = "比赛结束 · 成绩榜"
    rimg = stext(rib_t, px(15), WHITE, "disp", ow=px(2), oc=(110, 16, 24))
    rw, rh = rimg.get_width() + px(44), px(24)
    rx, ry = x0 + (PW - rw) // 2, TOP + px(50)
    surf.blit(plate(rw, rh, (238, 64, 64), (160, 20, 32), 255, slant=0, cuts=(px(8), px(8), px(8), px(8)), cut=0), (rx, ry))
    surf.blit(rimg, (rx + (rw - rimg.get_width()) // 2, ry + (rh - rimg.get_height()) // 2))
    sub = f"{physics.TRACKS[R.track]['label']} · {fx.MODE_LABEL.get(R.mode, R.mode)} · {R.sim_time:g} 秒"
    simg = fx.text(sub, px(12), MUTED)
    surf.blit(simg, (x0 + (PW - simg.get_width()) // 2, ry + rh + px(4)))
    n = len(R.lanes)
    y_top = TOP + px(98)
    avail = H - y_top - px(8)
    row_h = min(px(128), avail // n)
    y_start = y_top + (avail - row_h * n) // 3
    for r, i in enumerate(R.ranked()):
        lane = R.lanes[i]
        res = lane["result"]
        ts = (end_t - 0.15 * r) / 0.42
        if ts <= 0:
            continue
        slide = _ease_back(ts, 1.2)
        tc = _ease((end_t - 0.3 - 0.15 * r) / 1.2)
        cw, ch = PW - px(26), row_h - px(10)
        cx = x0 + px(16) + int((1 - slide) * PW)
        cy = y_start + r * row_h
        _board_row(R, surf, cx, cy, cw, ch, r, i, lane, res, tc, end_t - 0.3 - 0.15 * r - 1.2)


def _board_row(R, surf, x, y, w, h, r, i, lane, res, tc, t_after):
    color = R.colors[i]
    top = mix(NAVY_T, color, 0.12)
    _ro = os.environ.get("ARENA_RANK_OVERRIDE")            # 单人回放（P2）：榜里只有一行，名次牌按全场真实名次显示
    if _ro:
        r = max(0, int(_ro) - 1)
    band = (MEDAL[r], px(3)) if r < 3 else (color, px(2))
    surf.blit(plate(w, h, top, (18, 22, 40), 236, slant=px(12), band=band), (x, y))
    pygame.draw.polygon(surf, color, [(x + px(14), y + px(5)), (x + px(20), y + px(5)), (x + px(8), y + h - px(5)), (x + px(2), y + h - px(5))])
    # 名次奖牌（左侧竖直居中）
    md = min(px(52), h - px(30))
    surf.blit(medal(md, r, color), (x + px(20), y + (h - md) // 2 - px(4)))
    # 头像框 + 名字 / 模型 · 档位
    pd = min(px(44), h - px(64))
    ax = x + px(22) + md + px(6)
    surf.blit(portrait_or_badge(R, i, pd), (ax, y + px(8)))
    tx = ax + pd + px(8)
    gd = min(px(42), h - px(70))
    gx = x + w - px(18) - gd
    name_w = gx - px(8) - tx
    nm = fit_stext(lane["name"], name_w, px(20), px(14), fill=WHITE, kind="cn", ow=px(2))
    surf.blit(nm, (tx, y + px(7)))
    yy = y + px(7) + nm.get_height() - px(2)
    ep = effort_chip(lane["effort"], px(10)) if lane.get("effort") else None
    if ep and nm.get_width() + px(6) + ep.get_width() <= name_w:     # 档位标签跟在名字后面，模型名整行可用（不被截断）
        surf.blit(ep, (tx + nm.get_width() + px(6), y + px(7) + (nm.get_height() - ep.get_height()) // 2 + px(1)))
        ep = None
    if lane["model"]:
        mi = fit_stext(lane["model"], name_w - (ep.get_width() + px(5) if ep else 0), px(12), px(10), fill=(190, 198, 222), kind="cnr", ow=0, sh=0)
        surf.blit(mi, (tx, yy))
        if ep:
            surf.blit(ep, (tx + mi.get_width() + px(5), yy + (mi.get_height() - ep.get_height()) // 2))
    elif ep:
        surf.blit(ep, (tx, yy))
    # 评级徽章（计数结束后"盖章"弹入）+ 星级
    if t_after > -0.15:
        gimg, _gp = grade_badge(gd, lane["grade"])
        u = (t_after + 0.15) / 0.3
        s = 2.2 - 1.2 * _ease_back(u, 2.0) if u < 1 else 1.0
        a = int(255 * min(1.0, u * 2))
        img = gimg if s == 1.0 else pygame.transform.smoothscale(gimg, (max(2, int(gimg.get_width() * s)), max(2, int(gimg.get_height() * s))))
        if a < 255:
            img = img.copy()
            img.set_alpha(a)
        gcx, gcy = gx + gd // 2, y + px(6) + gd // 2
        surf.blit(img, (gcx - img.get_width() // 2, gcy - img.get_height() // 2))
        stars = GRADE_STARS.get(lane["grade"], 0)
        ssz = max(px(9), gd // 4)
        for j in range(3):
            st = icon("star" if j < stars else "star0", ssz)
            surf.blit(st, (gcx + (j - 1) * (ssz + px(1)) - ssz // 2, y + px(6) + gd + px(1)))
    # 两栏：成绩（计数）| 冲线用时（单独一栏）
    hh = px(30)
    sy = y + h - hh - px(22)
    area = x + w - px(16) - ax
    sc_w = int(area * 0.54)
    _stat(surf, ax, sy, sc_w, hh, "成绩", ntext(f"{res['score'] * tc:.2f}m", px(22), grad=(GOLD_HI, GOLD_LO), ow=px(2)))
    fin = res["finish_time"]
    if lane["load_error"]:
        fimg = stext("载入失败", px(13), (255, 140, 130), "cn", ow=px(1), sh=0)
    elif fin is not None:
        fimg = ntext(f"{fin * tc:.2f}s", px(20), WHITE, ow=px(2))
    else:
        fimg = stext("未冲线", px(13), MUTED, "cn", ow=px(1), sh=0)
    _stat(surf, ax + sc_w + px(5), sy, area - sc_w - px(5), hh, "冲线", fimg, flag=fin is not None)
    # 细节一行：实际距离 · 摔倒 · 撞栏（左）| 评级称号（右，评级色）
    nf, nh = len(lane["fx"].falls), len(lane["fx"].hurdle_hits)
    gtxt = next(t for th, g, t in evaluator.GRADES if g == lane["grade"])
    gimg2 = fx.text(f"{lane['grade']} · {gtxt}", px(11), mix(GRADE_COL.get(lane["grade"], STEEL), WHITE, 0.35), True)
    det = f"实际 {res['final']['x']:.2f}m" + (f" · 摔倒 {nf}" if nf else "") + (f" · 撞栏 {nh}" if nh else "")
    dimg = hud.fit(det, area - gimg2.get_width() - px(10), px(11), (160, 170, 196))
    dy = y + h - px(18)
    surf.blit(dimg, (ax, dy))
    surf.blit(gimg2, (x + w - px(16) - gimg2.get_width(), dy))


def _stat(surf, x, y, w, hh, label, val, flag=False):
    surf.blit(plate(w, hh, (14, 16, 30), (28, 32, 54), 230, ow=px(1), cut=px(4), shadow=False, gloss=False), (x, y))
    lab = fx.text(label, px(10), (150, 160, 190), True)
    surf.blit(lab, (x + px(6), y + (hh - lab.get_height()) // 2))
    lx = x + px(8) + lab.get_width()
    if flag:
        ic = icon("flag", px(14))
        surf.blit(ic, (lx, y + (hh - ic.get_height()) // 2))
        lx += ic.get_width() + px(2)
    vx = max(lx + px(2), x + w - val.get_width() - px(6))
    surf.blit(val, (vx, y + (hh - val.get_height()) // 2))


# ------------------------------------------------------------ 悬停 / 帮助 --
def draw_hover(R, surf, pos, k):
    W, H, TOP, ROW_A = V.W, V.H, V.TOP, V.ROW_A
    mx, my = pos
    tl = R.timeline_rect()
    if ROW_A <= my < TOP and tl.x - px(8) <= mx <= tl.right + px(8):
        kk = R.frame_at_x(mx)
        img = ntext(f"{kk / FPS:.2f}s", px(14), WHITE, ow=px(1))
        w, h = img.get_width() + px(24), img.get_height() + px(8)
        x = max(px(10), min(W - w - px(10), mx - w // 2))
        surf.blit(plate(w, h, slant=px(6), cut=px(3), band=(GOLD, px(2))), (x, TOP + px(4)))
        surf.blit(img, (x + (w - img.get_width()) // 2 + px(2), TOP + px(4) + (h - img.get_height()) // 2))
        return
    i = R.lane_at(pos)
    if i is None:
        return
    lane = R.lanes[i]
    f = lane["frames"][k]
    ml = hud.model_line(lane["model"], lane.get("effort", ""))
    lines = [(lane["name"] + (f" ({ml})" if ml else ""), True),
             (f"时间 {f['time']:.2f}s", False), (f"实际 x {f['x']:.3f} m", False), (f"成绩 {f['score']:.3f} m", False),
             (f"速度 vx {f['vx']:+.2f} m/s", False), (f"躯干角 {math.degrees(f['torso_angle']):+.1f}°", False),
             (f"躯干高度 {f['y']:.2f} m", False), (f"姿态 {V.label_text(lane['labels'][k])}", False)]
    imgs = [fx.text(s, px(14), WHITE if b else (214, 222, 240), b) for s, b in lines]
    bw = max(im.get_width() for im in imgs) + px(26)
    bh = sum(im.get_height() for im in imgs) + px(18)
    x = mx + px(18) if mx + px(18) + bw < W else mx - px(18) - bw
    y = max(TOP + px(4), min(H - bh - px(4), my + px(12)))
    surf.blit(plate(bw, bh, cut=px(8), band=(R.colors[i], px(3))), (x, y))
    yy = y + px(10)
    for im in imgs:
        surf.blit(im, (x + px(12), yy))
        yy += im.get_height()


HELP_ROWS = [("空格", "暂停 / 继续"), ("← / →", "暂停时逐帧，播放中 ±2 秒"), ("↑ / ↓ / 滚轮", "倍速 0.25x ~ 4x"),
             (", / .", "跳到上一个 / 下一个事件（摔倒·撞栏·冲线）"), ("B", "慢动作开关"), ("F", "追拍：自动放大领先者"),
             ("G", "切换 叠放 / 四宫格"), ("J", "关节波形小面板：关 → 4 条 → 全部 6 条"), ("TAB", "显示 / 隐藏 HUD"),
             ("S", "截图保存到当前文件夹"), ("R", "从头重播"), ("H", "显示 / 隐藏本帮助"), ("Q / Esc", "退出"),
             ("鼠标拖动时间轴", "跳到任意时刻"), ("鼠标点击赛道", "放大该赛道（再点还原）"), ("鼠标悬停赛道", "查看精确数值")]


def draw_help(R, surf):
    W, H, TOP = V.W, V.H, V.TOP
    bw, rh = px(640), px(26)
    bh = px(78) + len(HELP_ROWS) * rh
    x, y = (W - bw) // 2, TOP + (H - TOP - bh) // 2
    surf.blit(plate(bw, bh, (34, 40, 70), (14, 16, 30), 250, cut=px(14), band=(GOLD, px(3))), (x, y))
    t = stext("操作说明", px(24), WHITE, "disp", grad=(GOLD_HI, GOLD_LO), ow=px(2))
    surf.blit(t, (x + (bw - t.get_width()) // 2, y + px(12)))
    for j, (key, desc) in enumerate(HELP_ROWS):
        yy = y + px(60) + j * rh
        kc = chip(key, px(13), (70, 82, 120), kind="cn")
        surf.blit(kc, (x + px(30), yy))
        surf.blit(fx.text(desc, px(15), (226, 232, 246)), (x + px(210), yy + px(1)))


# ------------------------------------------------------------ 片头 VS ------
_intro_sources = _LRU(1)
_intro_columns = _LRU(16)


def _intro_texture():
    path = os.path.join(UI_DIR, "intro_motion_texture.png")

    def load():
        try:
            return pygame.transform.grayscale(pygame.image.load(path).convert())
        except (OSError, pygame.error) as exc:
            logging.getLogger(__name__).warning("片头纹理无法加载，回退原斜纹：%s (%s)", path, exc)
            return False
    return _intro_sources.get_or(path, load)


def _intro_surface(w, h, color, column):
    g = vgrad(w, h, mix(color, WHITE, 0.18), shade(color, 0.42)).copy()
    texture = _intro_texture()
    if texture is False:
        for x in range(-h, w, px(18)):
            pygame.draw.line(g, (*mix(color, WHITE, 0.3), 255), (x, h), (x + h, 0), px(3))
        return g
    factor = max(w / texture.get_width(), h / texture.get_height())
    size = (max(w, round(texture.get_width() * factor)), max(h, round(texture.get_height() * factor)))
    tile = pygame.transform.smoothscale(texture, size)
    # 只以灰度调制明暗；队伍色保留。v2 把调制范围从 176~255 拉到 135~255，宽缓纹理看得出来，又不和头像、文字抢对比。
    tile.fill((120, 120, 120), special_flags=pygame.BLEND_RGB_MULT)
    tile.fill((135, 135, 135), special_flags=pygame.BLEND_RGB_ADD)
    crop_x = round((size[0] - w) * (0.25 + (column % 4) / 6))
    crop_y = (size[1] - h) // 2
    g.blit(tile, (0, 0), (crop_x, crop_y, w, h), special_flags=pygame.BLEND_RGB_MULT)
    return g


def draw_intro(R, surf, t):
    """选手介绍 / 对阵画面（约 2 秒）：斜切分屏，每人一栏（队伍色渐变 + 大头像 + 名字 + 模型徽章），栏间 VS 砸入。"""
    W, H = V.W, V.H
    n = len(R.lanes)
    surf.blit(cached(("intro_bg", W, H), lambda: _intro_bg(W, H)), (0, 0))
    top, bot = px(112), H - px(64)
    sl = px(70) if n > 1 else 0
    gap = px(12)
    tw = W + sl
    colw = (tw - gap * (n - 1)) / n
    for i in range(n):
        ei = _ease((t - 0.08 - 0.12 * i) / 0.45)
        if ei <= 0:
            continue
        x_l = -sl + i * (colw + gap)
        off = (1 - ei) * (H if i % 2 == 0 else -H)
        _intro_col(R, surf, i, x_l, colw, top, bot, sl, off, t)
    for i in range(n - 1):                 # 栏间 VS
        tv = t - 0.72 - 0.08 * i
        if tv <= 0:
            continue
        seam = -sl + (i + 1) * (colw + gap) - gap / 2 + sl * 0.5
        vs = cached(("vs", px(60)), lambda: _vs_img(px(60)))
        s = 2.6 - 1.6 * _ease_back(tv / 0.22, 1.6) if tv < 0.22 else 1.0
        img = vs if s == 1.0 else pygame.transform.smoothscale(vs, (max(2, int(vs.get_width() * s)), max(2, int(vs.get_height() * s))))
        vy = top + (bot - top) * 0.40
        if tv < 0.12:
            fl = pygame.Surface((px(220), px(220)), pygame.SRCALPHA)
            pygame.draw.circle(fl, (255, 255, 230, int(200 * (1 - tv / 0.12))), (px(110), px(110)), px(110))
            surf.blit(fl, (seam - px(110), vy - px(110)))
        surf.blit(img, (seam - img.get_width() // 2, vy - img.get_height() // 2))
    e = _ease(t / 0.45)
    title = stext("AI 运动会", px(52), WHITE, "disp", grad=(GOLD_HI, GOLD_LO), ow=px(4), oc=INK, sh=px(4))
    surf.blit(title, (W // 2 - title.get_width() // 2, int(px(10) - (1 - e) * px(120))))
    sub = f"{physics.TRACKS[R.track]['label']} · {fx.MODE_LABEL.get(R.mode, R.mode)} · {R.sim_time:g} 秒 · 终点 {physics.TRACK_LENGTH:g} 米"
    e2 = _ease((t - 0.25) / 0.4)
    if e2 > 0:
        img = chip(sub, px(17), (60, 70, 120), kind="cn")
        img = img.copy()
        img.set_alpha(int(255 * e2))
        surf.blit(img, (W // 2 - img.get_width() // 2, px(74)))
    if t > 1.1:
        rd = cached(("intro_ready", px(38)), lambda: glowed(
            stext("READY?", px(38), WHITE, "num", grad=(GOLD_HI, GOLD_LO), ow=px(3), oc=INK, sh=px(3)), (255, 170, 60), px(6), 1)[0])
        a = _ease((t - 1.1) / 0.25)
        s = 1.0 + 0.05 * math.sin((t - 1.35) * 9) if t > 1.35 else 0.85 + 0.15 * a     # 弹入后轻轻呼吸
        img = rd if s == 1.0 else pygame.transform.smoothscale(rd, (max(2, int(rd.get_width() * s)), max(2, int(rd.get_height() * s))))
        img = img.copy()
        img.set_alpha(int(255 * a))
        surf.blit(img, (W // 2 - img.get_width() // 2, H - px(32) - img.get_height() // 2))


def _intro_bg(W, H):
    """深蓝底 + 几道很宽的柔光斜带 + 暗角（v2：原来的密集斜纹太花，和面板抢眼）。"""
    s = vgrad(W, H, (24, 30, 64), (6, 8, 20)).copy()
    bands = pygame.Surface((W, H), pygame.SRCALPHA)
    for k, x in enumerate(range(-H, W, px(300))):
        bw = px(110) if k % 2 else px(60)
        pygame.draw.polygon(bands, (120, 150, 255, 16), [(x, H), (x + bw, H), (x + bw + H, 0), (x + H, 0)])
    s.blit(pygame.transform.gaussian_blur(bands, px(24), False), (0, 0))
    vw, vh = 64, 36
    vig = pygame.Surface((vw, vh), pygame.SRCALPHA)
    for yy in range(vh):
        for xx in range(vw):
            d = math.hypot((xx + 0.5) / vw - 0.5, ((yy + 0.5) / vh - 0.42) * 0.9) / 0.62
            vig.set_at((xx, yy), (0, 0, 0, int(200 * max(0.0, min(1.0, d)) ** 2)))
    s.blit(pygame.transform.smoothscale(vig, (W, H)), (0, 0))
    return s


def _intro_mask(w, hh, sl):
    def make():
        m = pygame.Surface((w, hh), pygame.SRCALPHA)
        pygame.draw.polygon(m, WHITE, [(sl, 0), (w, 0), (w - sl, hh), (0, hh)])
        return m
    return cached(("intro_mask", w, hh, sl), make)


def _intro_shine(surf, x, y, w, hh, sl, u):
    """落位后的一道斜向扫光，u 为 0→1 的进度。"""
    band = pygame.Surface((w, hh), pygame.SRCALPHA)
    lean, peak = hh * 0.35, math.sin(math.pi * u)
    bx = -px(140) - lean + u * (w + px(280) + 2 * lean)
    for half, a in ((px(120), 14), (px(80), 16), (px(45), 20), (px(18), 26)):      # 叠几层，中间亮、两侧渐隐
        pygame.draw.polygon(band, (255, 255, 255, int(a * peak)),
                            [(bx + lean - half, 0), (bx + lean + half, 0), (bx + half, hh), (bx - half, hh)])
    band.blit(_intro_mask(w, hh, sl), (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    surf.blit(band, (x, y))


def _vs_img(size):
    img = stext("VS", size, WHITE, "num", grad=((255, 236, 150), (236, 70, 40)), ow=px(4), oc=INK, sh=px(3))
    g, _p = glowed(img, (255, 120, 60), px(8), 2)
    return g


def _intro_col(R, surf, i, x_l, colw, top, bot, sl, off, t):
    color = R.colors[i]
    lane = R.lanes[i]
    hh = int(bot - top)
    w = int(colw + sl)
    key = ("intro_col", i, w, hh, sl, lane["name"], lane["model"], lane.get("effort", ""),
           tuple(color), tuple(R.run_colors[i]), R.chars[i])

    def make():
        S = pygame.Surface((w, hh), pygame.SRCALPHA)
        pts = [(sl, 0), (w, 0), (w - sl, hh), (0, hh)]
        g = _intro_surface(w, hh, color, i)
        g.blit(vgrad(w, hh, (0, 0, 0, 0), (0, 0, 0, 120)), (0, 0))        # 下沉暗部，托住名字
        num = stext(str(i + 1), int(hh * 0.40), WHITE, "num", ow=0, sh=0).copy()    # 道次大号水印，填满下半栏（copy：stext 结果是共享缓存）
        num.set_alpha(30)
        g.blit(num, (w - sl - num.get_width() - px(14), hh - num.get_height() * 0.86))
        g.blit(_intro_mask(w, hh, sl), (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        S.blit(g, (0, 0))
        pygame.draw.polygon(S, INK, pts, px(4))
        pygame.draw.line(S, (255, 255, 255), (sl + px(4), px(5)), (w - px(4), px(5)), px(2))
        cx = w / 2
        ch = R.chars[i]
        d = int(min(colw * 0.72, hh * 0.46, px(270)))
        ay = int(hh * 0.34)
        glow_d = int(d * 1.75)                                               # 头像背后的主题色光晕
        glow = pygame.Surface((glow_d, glow_d), pygame.SRCALPHA)
        glow.fill((*mix(color, WHITE, 0.55), 0))
        pygame.draw.circle(glow, (*mix(color, WHITE, 0.55), 110), (glow_d / 2, glow_d / 2), glow_d * 0.30)
        glow = pygame.transform.gaussian_blur(glow, max(1, int(glow_d * 0.09)), False)   # 模糊出柔和衰减，外缘不留硬边
        S.blit(glow, (cx - glow_d / 2, ay - glow_d / 2))
        if ch is not None and ch.head is not None:
            ring = pygame.Surface((d + px(16), d + px(16)), pygame.SRCALPHA)
            c = ring.get_width() / 2
            pygame.draw.circle(ring, INK, (c, c), c)
            pygame.draw.circle(ring, WHITE, (c, c), c - px(3))
            pygame.draw.circle(ring, color, (c, c), c - px(7))
            ring.blit(ch.portrait(d, shade(color, 0.7), px(3)), (px(8), px(8)))
            S.blit(ring, (cx - c, ay - c))
        else:
            sc = min(pf(90.0), colw / 2.6)
            stand = type("V", (), {})()
            stand.scale = sc
            stand.ax = cx - lane["poses"][0][0][0] / PPM * sc
            stand.ay = ay + sc * 1.05
            R._draw_runner(S, stand, lane["poses"][0], R.run_colors[i], "grin", 0, lane_i=i)
        tag = chip(f"LANE {i + 1}", px(14), shade(color, 0.6))
        S.blit(tag, (cx - tag.get_width() / 2, ay - d / 2 - px(34)))
        name = fit_stext(lane["name"], int(colw * 0.86), px(46), px(20), fill=WHITE, kind="disp", ow=px(4), oc=INK)
        ny = int(hh * 0.61)
        S.blit(name, (cx - name.get_width() / 2 - sl * (ny / hh - 0.5), ny))
        y2 = ny + name.get_height() + px(4)
        if lane["model"]:
            mimg = fit_stext(lane["model"], int(colw * 0.78), px(18), px(12), fill=WHITE, kind="cn", ow=px(2), oc=shade(color, 0.3))
            pw = mimg.get_width() + px(30)
            pl = plate(pw, mimg.get_height() + px(10), (30, 34, 60), (12, 14, 28), 235, slant=px(10), cut=px(3))
            xx = cx - pw / 2 - sl * (y2 / hh - 0.5)
            S.blit(pl, (xx, y2))
            S.blit(mimg, (xx + (pw - mimg.get_width()) / 2 + px(3), y2 + px(5)))
            y2 += pl.get_height() + px(4)
        if lane.get("effort"):
            ep = effort_chip(lane["effort"], px(15))
            S.blit(ep, (cx - ep.get_width() / 2 - sl * (y2 / hh - 0.5), y2))
        return S
    img = _intro_columns.get_or(key, make)
    surf.blit(img, (int(x_l), int(top + off)))
    u = (t - (0.08 + 0.12 * i + 0.45) - 0.1) / 0.5        # 落位 0.1 秒后扫一道光
    if 0 < u < 1:
        _intro_shine(surf, int(x_l), int(top + off), w, hh, sl, u)


# ------------------------------------------------------------ 地面路牌 ----
def paint_zone_sign(s, base, tip, zname, sfs, col, scale):
    """区域路牌（画进地面分块；分块是 colorkey 透明，所以这里只画不透明像素）：深色立柱 + 斜切彩色牌 + 白字描边 + 立体厚边。"""
    pw = max(px(3), int(scale * 0.03))
    pygame.draw.line(s, INK, base, tip, pw + px(2))
    pygame.draw.line(s, (150, 156, 176), base, tip, pw)
    img = stext(zname, sfs, WHITE, "cn", ow=max(1, px(1) + sfs // 14), oc=shade(col, 0.35), sh=0)
    bh = img.get_height() + px(6)
    sl = int(bh * 0.38)
    bw = img.get_width() + px(14) + sl
    x, y = int(tip[0] - bw / 2), int(tip[1] - bh)
    depth = px(3)
    pygame.draw.polygon(s, INK, [(px_ + x + depth * 0.6, py_ + y + depth) for px_, py_ in _shape(bw, bh, (px(3),) * 4, sl)])
    pl = plate(bw, bh, mix(col, WHITE, 0.3), shade(col, 0.78), 255, ow=px(2), cut=px(3), slant=sl, shadow=False)
    s.blit(pl, (x, y))
    s.blit(img, (x + (bw - img.get_width()) // 2 + sl // 3, y + (bh - img.get_height()) // 2))
