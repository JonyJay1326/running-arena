"""回放 HUD 小部件（2026-10-28 改版）：选手卡（名字 + 模型 + 推理强度）、触地小人图、文字适配。
纯绘制，不碰模拟/成绩，也不用随机数。原版 HUD 备份在 engine/_ui_backup/。

--names 写法（向后兼容）：
  "名字"                       只有名字
  "名字|模型"                  如 "Codex|gpt-6.1-sol"
  "名字|模型 强度"             末尾一个词是已知推理强度（low/medium/high/xhigh/max/…）时自动拆出，如 "Codex|gpt-6.1-sol high"
  "名字|模型|强度"             显式写强度，如 "Qoder|Kimi-K3.1-Pro|high"
"""
import pygame

from viz_fx import INK, mix, panel, shade, text

EFFORTS = {"minimal", "low", "medium", "mid", "high", "xhigh", "max", "thinking", "nothink", "none", "auto",
           "低", "中", "高", "超高", "最高"}
SUB = (88, 96, 112)            # 卡片上的次要文字（模型名）
MUTED = (120, 126, 140)


def parse_entry(raw):
    """'名字|模型|强度' / '名字|模型 强度' / '名字|模型' / '名字' → (名字, 模型, 强度)。"""
    parts = [p.strip() for p in raw.split("|")]
    name = parts[0]
    model = parts[1] if len(parts) > 1 else ""
    effort = "|".join(parts[2:]).strip() if len(parts) > 2 else ""
    if not effort and " " in model:
        head, _, last = model.rpartition(" ")
        if last.lower() in EFFORTS:
            model, effort = head.strip(), last
    return name, model, effort


def model_line(model, effort, sep=" · "):
    return sep.join(s for s in (model, effort) if s)


def fit(s, max_w, size, color, bold=False, min_size=None):
    """先缩小字号（不低于 min_size），还放不下就截断加省略号。返回渲染好的图。"""
    min_size = min_size or size
    fs = size
    img = text(s, fs, color, bold)
    while img.get_width() > max_w and fs > min_size:
        fs -= 1
        img = text(s, fs, color, bold)
    if img.get_width() <= max_w:
        return img
    lo, hi = 0, len(s)
    while lo < hi:                 # 二分最长能放下的前缀
        mid = (lo + hi + 1) // 2
        if text(s[:mid].rstrip() + "…", fs, color, bold).get_width() <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return text((s[:lo].rstrip() + "…") if lo else "…", fs, color, bold)


def pill(s, size, fg, bg, pad_x=None, outline=None):
    """圆角药丸标签（缓存在 text 缓存之外，每次新建；调用方量小）。"""
    img = text(s, size, fg, True)
    px = pad_x if pad_x is not None else max(5, size // 2)
    surf = pygame.Surface((img.get_width() + 2 * px, img.get_height() + 2), pygame.SRCALPHA)
    r = surf.get_rect()
    pygame.draw.rect(surf, bg, r, border_radius=r.h // 2)
    if outline:
        pygame.draw.rect(surf, outline, r, 1, border_radius=r.h // 2)
    surf.blit(img, (px, 1))
    return surf


_pill_cache = {}


def cpill(s, size, fg, bg, outline=None):
    key = (s, size, fg, bg, outline)
    p = _pill_cache.get(key)
    if p is None:
        if len(_pill_cache) > 500:
            _pill_cache.clear()
        p = _pill_cache[key] = pill(s, size, fg, bg, outline=outline)
    return p


def lane_badge(surf, center, r, num, color):
    pygame.draw.circle(surf, shade(color, 0.7), (center[0], center[1] + 1), r)
    pygame.draw.circle(surf, color, center, r)
    pygame.draw.circle(surf, (255, 255, 255), center, r, max(1, r // 6))
    img = text(str(num), max(9, int(r * 1.25)), (255, 255, 255), True)
    surf.blit(img, (center[0] - img.get_width() // 2, center[1] - img.get_height() // 2))


# 触地小人图：躯干在上、双手在两侧、双脚在下（亮绿 = 着地，躯干着地亮红）
_PICTO = {"torso": (0.0, 0.0), "hand_L": (-1.0, 0.55), "hand_R": (1.0, 0.55), "foot_L": (-0.5, 1.45), "foot_R": (0.5, 1.45)}


def contact_picto(surf, cx, top, u, contact, on=(40, 175, 75), down=(215, 45, 45)):
    """u：单位长度(px)。返回占用高度。"""
    pts = {k: (cx + x * u, top + u * 0.45 + y * u) for k, (x, y) in _PICTO.items()}
    line = (150, 156, 168)
    t = pts["torso"]
    for k in ("hand_L", "hand_R", "foot_L", "foot_R"):
        pygame.draw.line(surf, line, t, pts[k], max(1, int(u * 0.18)))
    for k, p in pts.items():
        r = u * (0.5 if k == "torso" else 0.36)
        lit = contact.get(k)
        col = (down if k == "torso" else on) if lit else (226, 229, 235)
        pygame.draw.circle(surf, col, p, r)
        pygame.draw.circle(surf, shade(col, 0.7) if lit else (140, 146, 158), p, r, max(1, int(u * 0.12)))
    return int(u * 2.4)


def lane_card(surf, rect, num, color, name, model, effort, score, actual, contact, tier):
    """赛道左上角选手卡。tier: "full" | "mid"。返回卡片矩形（状态标签挂在它右边）。
    宽度随内容变化（196~330px），名字/模型太长时先缩字号再加省略号。"""
    if tier == "full":
        fs_name = max(17, min(22, int(rect.h * 0.064)))
    else:
        fs_name = max(15, min(17, int(rect.h * 0.1)))
    fs_model = max(12, fs_name - 5)
    max_w = int(max(196, min(330, rect.w * 0.44)))
    pad = 8
    x0, y0 = rect.x + 6, rect.y + 6
    br = (text("8", fs_name, INK, True).get_height()) // 2           # 赛道号徽章半径
    left = pad + 6                                                    # 文字左缩进（色条右边）
    # 先排版测量
    eff_img = cpill(effort, fs_model - 1, (255, 255, 255), shade(color, 0.85)) if effort else None
    lab = text("成绩", fs_model, MUTED, True)
    sc_img = text(f"{score:.2f}", fs_name + 3, INK, True)
    unit = text("m", fs_model, INK, True)
    diff = abs(actual - score) >= 0.005
    act_img = text(f"实际 {actual:.2f}", fs_model - 1, (200, 70, 60), True) if diff else None
    score_w = lab.get_width() + 4 + sc_img.get_width() + 2 + unit.get_width()
    row1 = max(text("名", fs_name, INK, True).get_height(), 2 * br)
    row2 = max(text("m", fs_model, INK).get_height(), eff_img.get_height() if eff_img else 0) if (model or effort) else 0
    row3 = sc_img.get_height() - 4
    ch = pad + row1 + (row2 + 1 if row2 else 0) + row3 + pad - 2
    u = min(13.0, (ch - 2 * pad) / 2.5)
    picto_w = int(u * 2.6) + 6
    text_max = max_w - left - pad - picto_w
    name_img = fit(name, text_max - 2 * br - 6, fs_name, INK, True, min_size=max(12, fs_name - 5))
    mod_room = text_max - (eff_img.get_width() + 5 if eff_img else 0)
    model_img = fit(model, mod_room, fs_model, SUB, False, min_size=fs_model - 1) if model else None
    mw = (model_img.get_width() if model_img else 0) + (eff_img.get_width() + (5 if model_img else 0) if eff_img else 0)
    want = max(2 * br + 6 + name_img.get_width(), mw, score_w + (8 + act_img.get_width() if act_img else 0))
    cw = max(196 if tier == "full" else 180, min(max_w, left + want + 10 + picto_w + pad))
    card = pygame.Rect(x0, y0, cw, ch)
    surf.blit(panel(cw, ch, (255, 255, 255), 218, 10), card.topleft)
    pygame.draw.rect(surf, color, (card.x, card.y + 6, 4, ch - 12), border_radius=2)
    # 行 1：徽章 + 名字
    tx = card.x + left
    yy = card.y + pad - 2
    lane_badge(surf, (tx + br - 2, yy + row1 // 2), br, num, color)
    surf.blit(name_img, (tx + 2 * br + 4, yy + (row1 - name_img.get_height()) // 2))
    yy += row1
    # 行 2：模型 + 强度药丸
    if row2:
        mx = tx
        if model_img:
            surf.blit(model_img, (mx, yy + (row2 - model_img.get_height()) // 2))
            mx += model_img.get_width() + 5
        if eff_img:
            surf.blit(eff_img, (mx, yy + (row2 - eff_img.get_height()) // 2))
        yy += row2 + 1
    # 行 3：成绩（大）+ 实际距离（与成绩不同时才显示，红字）
    base = yy + sc_img.get_height() - 6
    surf.blit(lab, (tx, base - lab.get_height() + 1))
    sx = tx + lab.get_width() + 4
    surf.blit(sc_img, (sx, base - sc_img.get_height() + 4))
    sx += sc_img.get_width() + 2
    surf.blit(unit, (sx, base - unit.get_height() + 1))
    sx += unit.get_width() + 8
    if act_img and sx + act_img.get_width() < card.right - picto_w - pad:
        surf.blit(act_img, (sx, base - act_img.get_height()))
    # 触地小人图（右侧，整张卡竖直居中）
    contact_picto(surf, card.right - pad - picto_w // 2, card.y + (ch - u * 2.4) / 2 - 1, u, contact)
    return card


def strip_card(surf, rect, num, color, name, score, fs):
    """极小赛道（追拍小窗等）：底部一行 —— 徽章 + 名字 + 成绩。"""
    name_img = fit(name, int(rect.w * 0.5), fs, INK, True, min_size=11)
    sc = text(f"{score:.2f} m", fs, INK, True)
    br = name_img.get_height() // 2 - 1
    w = 10 + 2 * br + 6 + name_img.get_width() + 10 + sc.get_width() + 10
    h = name_img.get_height() + 6
    r = pygame.Rect(rect.x + 6, rect.bottom - h - 6, w, h)
    surf.blit(panel(w, h, (255, 255, 255), 214, h // 2), r.topleft)
    lane_badge(surf, (r.x + 6 + br, r.centery), br, num, color)
    surf.blit(name_img, (r.x + 6 + 2 * br + 6, r.y + 3))
    surf.blit(sc, (r.right - sc.get_width() - 10, r.y + 3))
    return r


def status_tag(s, size, bg):
    return cpill(s, size, (255, 255, 255), bg, outline=mix(bg, (255, 255, 255), 0.55))
