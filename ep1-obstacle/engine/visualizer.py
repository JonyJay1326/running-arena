"""同屏回放：python engine/visualizer.py bot1.py [bot2.py ...] [--track obstacle|flat] [--mode normal|ice|moon]
          [--time 30] [--speed 1.0] [--names "Claude|Opus 5.5" B ...] [--layout stack|grid] [--follow]
          [--auto-slowmo] [--no-fx] [--bubbles few|normal|off] [--save out.mp4 [--intro]] [--screenshot a.png --at 12]
--names 写法："名字" / "名字|模型" / "名字|模型 high" / "名字|模型|high"（解析见 viz_hud.parse_entry；2026-10-28 HUD 改版，原版在 engine/_ui_backup/）

每个选手在自己独立的 Runner 里先用 evaluator.simulate 无头跑完（与自测逐帧一致），
通过 on_step 回调记录每帧所有刚体的位姿，再按任意倍速回放。所有特效都由录制数据 + 独立的
random.Random(12345) 预先算好，画面只取决于回放位置，回退/拖动/重播完全确定，不影响成绩。
回放位置是浮点帧号：慢动作/非整数倍速时在相邻两帧录制位姿（最短弧插角度）、镜头、震屏、粒子时间之间插值，
整数帧位置的画面与不插值时逐字节一致（--at 截图、逐帧、拖动、重播都落在整数帧）。

键盘：空格 暂停 | ←/→ 逐帧（播放中为 ±2 秒）| ↑/↓ 或滚轮 倍速 | ,/. 上/下一个事件（摔倒/撞栏/冲线）
      B 慢动作 0.25x | F 追拍领先者 | G 叠放/四宫格 | J 全部 6 个关节波形 | TAB HUD | S 截图 | H 帮助 | R 重播 | Q/Esc 退出
鼠标：点击/拖动顶部时间轴跳转 | 悬停赛道看精确数值 | 点击赛道放大（再点还原）
导出：--save out.mp4 离屏渲染（libx264 yuv420p 1280x720 60fps），--intro 加 2 秒片头，结束成绩榜停留 3 秒
截图：--screenshot a.png --at 12.5（--at end 为结束画面），调试用
角色化（2026-10-29，可选）：--avatars Opus Astra Sonnet 千问（按赛道顺序；auto = 从名字/文件名推断；- = 该道不用）
      [--colors "#ff8800" - ...] [--avatar-dir 目录] [--no-separate]
      头像贴头（随躯干旋转，旋转结果按 2° 缓存）、主题色球衣、头像信息卡、Q 版表情弹窗（素材约定见 viz_fx 末尾）。
      不加 --avatars/--colors 时画面与改版前逐字节一致。
1080p / 新画法（2026-10-29，可选）：--res 1080 在 1920x1080 画布上原生绘制（所有 720p 像素常数经 viz_fx.px()/pf() 乘 S=1.5），
      导出直接 1920x1080；--body-style v2 换用 viz_body2 的运动服 Q 版小人。不加这两个参数时画面逐字节不变。
游戏风格界面（2026-10-29，可选）：--ui game 换成 viz_ui_game 的 HUD（选手铭牌、名次盾牌、赛道缩略图、伤害数字、FINISH 横幅、
      结算界面、VS 片头；导出时第 0 帧前加 3-2-1-GO，--no-countdown 关）；关节波形变成可选小面板（J 键 / --waves）。
      截图可用 --at intro:1.2 / --at countdown:0.5。不加 --ui game 时画面逐字节不变。
"""
import argparse
import json
import math
import os
import random
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402

import evaluator  # noqa: E402
import physics  # noqa: E402
import viz_fx as fx  # noqa: E402
import viz_hud as hud  # noqa: E402  选手卡/文字适配（2026-10-28 HUD 改版，原版在 engine/_ui_backup/）
import viz_runner  # noqa: E402  选手造型（2026-10-27 改版，见 viz_runner.py 顶部说明）
import viz_body2  # noqa: E402  --body-style v2：运动服 Q 版小人（2026-10-29，见 viz_body2.py 顶部说明）
import viz_robot  # noqa: E402  --body-style robot：机器人（CRT 电视头 + 工业风骨架，头像嵌进屏幕；见 viz_robot.py 顶部说明）
import viz_ui_game as gui  # noqa: E402  --ui game：游戏风格界面（2026-10-29，见 viz_ui_game.py 顶部说明）
import viz_game_scene as game_scene  # noqa: E402
from viz_fx import DOWN_RED, GOLD, INK, mix, pf, px, shade, text  # noqa: E402

PPM = physics.PPM
FPS = physics.FPS
W, H = 1280, 720
ROW_A = 60                  # 顶栏上半：标题 + 0~16m 进度
TOP = 84                    # 顶栏总高（下半是可拖动的时间轴）
PANEL_W = 360               # 结束成绩榜宽度（出现时赛道区让出这块）
SUB_BAND = 0                # 底部字幕带高（--sub-band，720p 单位；0 = 不留）：赛道区整体上收，口播字幕放在带里不挡选手
WAVE_SECONDS = 3.0
SPEEDS = [0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0]
END_HOLD = 3.0              # 导出视频时结束画面停留秒数
INTRO_SECONDS = 2.0
EVENT_LEAD = 30             # ,/. 跳到事件前 0.5 秒
SLOW = 0.25                 # 慢动作倍率（--slow-factor 可改；B 键和 --auto-slowmo 共用）
# 自动慢动作（--auto-slowmo）：哪些事件触发、事件前后各多少帧（--slow-events / --slow-window 可改；默认 = 摔倒前 0.5s 后 1s）
SLOW_EVENTS = ("fall",)
SLOW_BEFORE, SLOW_AFTER = 30, 60
SLOW_RANGES = []            # --slow-ranges：按比赛时间手动指定的慢放段 [(起帧, 止帧, 倍率)]（剪辑排解说用，原生插值，不靠剪辑重复帧）
SLOW_EVENT_KINDS = ("fall", "hurdle", "finish", "first-fall", "first-hurdle")   # first-* = 每条赛道只取第一次
# 头像贴头（--avatars）：头像边长 = 2 × 物理头半径 × AV_SCALE（不小于 AV_MIN_D px），
# 头像图里脸心在图中心下方约 AV_FACE_Y × 边长（make_avatars.py 的固定比例裁剪），再整体上抬 AV_LIFT × 头半径
AV_SCALE = 1.8
AV_MIN_D = 34
AV_FACE_Y = 0.17
AV_LIFT = 0.1

RES = 720                   # 画布高：720（默认，1280x720）或 1080（--res 1080，1920x1080 原生绘制，见 set_res）
BODY_STYLE = "v1"           # 小人画法：v1 = viz_runner（默认），v2 = viz_body2（--body-style v2），robot = viz_robot（--body-style robot）


def set_res(h):
    """切换画布分辨率（启动时、画任何东西之前调用）。所有 720p 写死的像素都经 fx.px()/fx.pf() 按 S = h/720 缩放；
    h == 720 时什么都不变（逐字节一致）。"""
    global RES, W, H, ROW_A, TOP, PANEL_W, SUB_BAND
    band720 = SUB_BAND / (RES / 720.0) if RES else SUB_BAND
    RES = int(h)
    fx.set_scale(RES / 720.0)
    W, H = px(1280), px(720)
    ROW_A, TOP, PANEL_W = px(60), px(84), px(360)
    SUB_BAND = px(band720) if band720 else 0


def set_body_style(style):
    global BODY_STYLE
    BODY_STYLE = style


UI_STYLE = "classic"        # classic = 原界面（默认）；game = viz_ui_game 的游戏风格（--ui game）
DEFAULT_EFFORT = "high"     # 游戏风格下，--names 里写了模型但没写思考等级时默认显示的档位
COUNTDOWN = True            # --ui game 导出时在第 0 帧之前加 3-2-1-GO（--no-countdown 关掉）


def set_ui(style, countdown=True):
    """切换界面风格（启动时、画任何东西之前调用）。classic 时什么都不变（逐字节一致）。"""
    global UI_STYLE, COUNTDOWN
    UI_STYLE, COUNTDOWN = style, countdown
    fx.UI_GAME = style == "game"
    gui.bind(sys.modules[__name__])


LANE_COLORS = [(225, 70, 60), (50, 120, 230), (40, 170, 90), (230, 150, 30)]
JOINT_COLORS = [(230, 80, 80), (240, 160, 160), (60, 120, 230), (150, 190, 250), (60, 170, 90), (150, 220, 160)]
JOINT_SHORT = ["左髋", "左膝", "右髋", "右膝", "左肩", "右肩"]
BG = (24, 28, 36)
MEDALS = [(245, 196, 50), (200, 208, 220), (210, 132, 64)]
FALL_LABELS = ("back", "face", "faceslide", "split", "struggle")


# ------------------------------------------------------------ 预计算 ------
def _body_order(runner):
    """固定的刚体顺序：躯干、左腿、右腿、左臂、右臂、左右脚掌、栏架（序号见 viz_fx.FOOT_IDX / HURDLE0）。"""
    return [runner.torso, runner.thighs[0], runner.shins[0], runner.thighs[1], runner.shins[1],
            runner.arms[0], runner.forearms[0], runner.arms[1], runner.forearms[1],
            *runner.foot_bodies, *runner.hurdles]


def _pose(runner):
    return [(b.position.x, b.position.y, b.angle) for b in _body_order(runner)]


def _always_fail(_obs):
    raise evaluator.BotError("bot 载入失败")


def run_bot(path, name, mode, sim_time, track, model="", start=None, effort=""):
    """无头跑完一个选手，返回回放所需的全部数据。成绩与 evaluator 完全一致。"""
    load_error = None
    try:
        act = evaluator.load_bot(path)
    except (evaluator.BotError, SyntaxError, OSError) as e:
        load_error = str(e)
        act = _always_fail      # 与“act 每帧都抛异常”一样：沿用 REST_ACTION 原地站着
    poses = [_pose(physics.Runner(mode, track, start=start))]     # 第 0 帧（构建是确定性的，与 simulate 内部一致）
    result = evaluator.simulate(act, mode, sim_time, track, start=start, on_step=lambda r, _o: poses.append(_pose(r)))
    frames = result["frames"] + [result["final"]]    # frames[k] 对应 poses[k]
    frames[-1] = dict(frames[-1], score=result["score"])
    labels = evaluator.classify(frames)
    down = [evaluator.is_down(f) for f in frames]      # 与计分同一个倒地定义
    # 镜头：躯干 x 的指数平滑（从头算起，逐帧回退也一致）
    cam, c = [], frames[0]["x"]
    for f in frames:
        c += (f["x"] - c) * 0.08
        cam.append(c)
    grade = next(g for th, g, _t in evaluator.GRADES if result["score"] >= th)
    finish_k = None if result["finish_time"] is None else int(round(result["finish_time"] * FPS))
    return {"name": name, "model": model, "effort": effort, "path": path, "frames": frames, "poses": poses, "labels": labels,
            "down": down, "cam": cam, "result": result, "grade": grade, "load_error": load_error, "finish_k": finish_k}


def attach_fx(lanes, track=physics.DEFAULT_TRACK, bubbles="few"):
    """所有装饰事件用同一个独立的 Random(12345) 按赛道顺序生成，不碰全局 random。
    气泡排期（bubbles = few/normal/off）是纯函数，不用随机数。"""
    rng = random.Random(fx.FX_SEED)
    for L in lanes:
        L["fx"] = fx.LaneFX(L["frames"], L["poses"], L["down"], L["labels"], L["finish_k"], rng)
    fx.schedule_bubbles([L["fx"] for L in lanes], [L["frames"] for L in lanes], [L["down"] for L in lanes], track, bubbles)


def leader_track(lanes):
    """每帧的领先者（带迟滞：领先 0.2m 以上并保持 0.5 秒才换人），追拍模式用。"""
    cur, cand, run, out = 0, None, 0, []
    for k in range(len(lanes[0]["frames"])):
        sc = [L["frames"][k]["score"] for L in lanes]
        best = max(range(len(lanes)), key=lambda i: sc[i])
        if best != cur and sc[best] - sc[cur] > 0.2:
            run = run + 1 if best == cand else 1
            cand = best
            if run >= 30:
                cur, cand, run = best, None, 0
        else:
            cand, run = None, 0
        out.append(cur)
    return out


def shape_table(mode, track):
    """从模板 Runner 取每个形状的局部几何：{刚体序号: [(kind, 数据, 是否脚掌)]}。"""
    r = physics.Runner(mode, track)
    order = _body_order(r)
    out = {}
    for body, shape in r.parts():
        idx = order.index(body)
        if hasattr(shape, "get_vertices"):
            verts = [tuple(v) for v in shape.get_vertices()]
            is_foot = idx in fx.FOOT_IDX
            out.setdefault(idx, []).append(("poly", verts, is_foot))
        else:
            out.setdefault(idx, []).append(("circle", (tuple(shape.offset), shape.radius), False))
    return out


def label_text(lab):
    """LABELS 里的显示名去掉开头的 emoji（多数字体画不出来）。"""
    name = evaluator.LABELS[lab][0]
    return name.split(" ", 1)[1] if " " in name else name


# ------------------------------------------------------------ 视图 --------
def _scale_for(rect):
    return max(px(20), min(px(170), int(rect.h / 2.8)))     # px / m：地面以上留约 2.2m


def _anchor(rect):
    return 0.42 if rect.w >= pf(1100) else 0.5 if rect.w >= pf(800) else 0.56 if rect.w >= pf(500) else 0.62


class View:
    """一条赛道一帧的世界(m) → 屏幕(px) 变换（含镜头抖动）。"""
    __slots__ = ("rect", "scale", "ax", "ay")

    def __init__(self, rect, scale, cx, cy, ox=0.0, oy=0.0):
        self.rect, self.scale = rect, scale
        self.ax = rect.x + rect.w * _anchor(rect) + ox - cx * scale
        self.ay = rect.y + rect.h * 0.78 + oy + cy * scale

    def w2s(self, x, y):
        return (self.ax + x * self.scale, self.ay - y * self.scale)

    def x_range(self):
        return (self.rect.x - self.ax) / self.scale, (self.rect.right - self.ax) / self.scale


def _poly_screen(v, pose_b, verts):
    px, py, a = pose_b
    ca, sa = math.cos(a), math.sin(a)
    k, ax, ay = v.scale / PPM, v.ax, v.ay
    return [(ax + (px + x * ca - y * sa) * k, ay - (py + x * sa + y * ca) * k) for x, y in verts]


def _ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


# ------------------------------------------------------------ 帧间插值 ----
TAU = 2 * math.pi
SLOW_RAMP = 0.15            # 进出慢动作时倍速渐变的秒数


def lerp_pose(p0, p1, fr):
    """两帧位姿之间插值：位置线性，角度走最短弧。fr == 0 时调用方直接用 p0（保证逐字节一致）。"""
    out = []
    for (x0, y0, a0), (x1, y1, a1) in zip(p0, p1):
        d = (a1 - a0 + math.pi) % TAU - math.pi
        out.append((x0 + (x1 - x0) * fr, y0 + (y1 - y0) * fr, a0 + d * fr))
    return out


def frame_val(frames, k, fr, key):
    """frames[k][key] 与下一帧之间线性插值（fr == 0 时原样返回）。"""
    a = frames[k][key]
    return a if fr == 0 else a + (frames[k + 1][key] - a) * fr


def seq_val(seq, k, fr):
    a = seq[k]
    return a if fr == 0 else a + (seq[k + 1] - a) * fr


def ramp_mult(mult, target, dt):
    """慢动作倍率向目标值线性渐变，SLOW_RAMP 秒走完 1 → SLOW 全程。"""
    step = (1.0 - SLOW) * dt / SLOW_RAMP
    return min(target, mult + step) if mult < target else max(target, mult - step)


class UI:
    """交互状态；导出/截图用默认值。"""

    def __init__(self, layout="stack", fx_on=True, follow=False):
        self.layout, self.fx_on, self.follow = layout, fx_on, follow
        self.focus = None
        self.show_hud, self.all_joints, self.show_help = True, False, False
        self.paused, self.speed, self.slow, self.slow_active = False, 1.0, False, False
        self.hover = None
        self.end_t = None           # 到达最后一帧后经过的秒数（成绩榜动画），None = 未结束
        self.toast = None           # (文字, 剩余秒数)
        self.interactive = False    # 交互窗口才显示键盘提示（导出/截图里不出现 "H 帮助"）
        self.show_wave = True       # --ui game 下关节波形是可选小面板（J 键 / --waves 打开）；classic 不看这个


# ------------------------------------------------------------ 渲染 --------
class Renderer:
    LAYERS = [(5, True), (6, True), (1, True), (2, True), (0, False), (3, False), (4, False), (7, False), (8, False)]

    def __init__(self, lanes, mode, track, sim_time):
        self.lanes = lanes
        self.mode, self.track, self.sim_time = mode, track, sim_time
        self.parts = shape_table(mode, track)
        self.hurdle_parts = [(i, d) for i, lst in self.parts.items() if i >= fx.HURDLE0 for kind, d, _f in lst if kind == "poly"]
        self.n_frames = len(lanes[0]["frames"])
        self.leader = leader_track(lanes)
        ev = []
        for i, L in enumerate(lanes):
            ev += [(k, i, "fall") for k in L["fx"].falls] + [(k, i, "hurdle") for k in L["fx"].hurdle_hits]
            if L["finish_k"] is not None:
                ev.append((L["finish_k"], i, "finish"))
        self.events = sorted(ev)
        self.slow_windows = self._slow_windows()
        rr = random.Random(fx.FX_SEED + 999)
        self.rtab = [(rr.random(), rr.random(), rr.random()) for _ in range(257)]
        self.last_layout = []
        self.style = fx.STYLE[mode]
        self.initials = _initials([L["name"] for L in lanes])
        # 角色化（--avatars / --colors，见 setup_characters）：没开时 colors 就是原来的 LANE_COLORS，char 全是 None
        self.colors = [L.get("ui_color") or LANE_COLORS[i] for i, L in enumerate(lanes)]
        self.run_colors = [L.get("run_color") or self.colors[i] for i, L in enumerate(lanes)]
        self.chars = [L.get("char") for L in lanes]
        for i, L in enumerate(lanes):
            ch = self.chars[i]
            if ch is not None and L.get("theme"):
                main, accent = L["theme"]
                for far in (False, True):      # 球衣主色 + 点缀色：预先放进 viz_runner 的调色板缓存（键含赛道号，不影响别的赛道）
                    viz_runner._pal_cache[(self.run_colors[i], far, i)] = fx.runner_palette(main, accent, far)
            L["popups"] = fx.schedule_popups(L["fx"], L["frames"], L["down"], L["finish_k"], ch) if ch is not None else []
            at = [-1] * self.n_frames
            for j, (a, b, _e, _x) in enumerate(L["popups"]):
                for kk in range(a, min(b, self.n_frames)):
                    at[kk] = j
            L["popup_at"] = at
        self._popup_side = {}
        if UI_STYLE == "game":
            gui.prepare(self)

    def _slow_windows(self):
        """按 SLOW_EVENTS 从事件里挑出触发自动慢动作的帧，前后各取 SLOW_BEFORE / SLOW_AFTER 帧；
        first-fall / first-hurdle 每条赛道只取第一次。重叠的窗口在 in_slow_window 里自然合并（只慢一次）。"""
        picked, seen = [], set()
        for k, i, kind in self.events:
            use = kind in SLOW_EVENTS
            if f"first-{kind}" in SLOW_EVENTS and (i, kind) not in seen:
                use = True
            seen.add((i, kind))
            if use:
                picked.append(k)
        return [(k - SLOW_BEFORE, k + SLOW_AFTER) for k in picked]

    def in_slow_window(self, k):
        return any(a <= k <= b for a, b in self.slow_windows)

    def manual_slow(self, k):
        """--slow-ranges 指定的慢放倍率（不在任何段里返回 None；重叠取最慢）。"""
        fs = [f for a, b, f in SLOW_RANGES if a <= k <= b]
        return min(fs) if fs else None

    # -- 布局 --
    def layout(self, k, ui):
        n = len(self.lanes)
        area = pygame.Rect(0, TOP, W - (PANEL_W if ui.end_t is not None else 0), H - TOP - SUB_BAND)
        if n == 1:
            return [(0, area)]
        feat = ui.focus if ui.focus is not None else (self.leader[k] if ui.follow else None)
        if feat is not None:          # 主赛道放大，其余缩成底部的小窗
            sh = px(150)
            main = pygame.Rect(area.x, area.y, area.w, area.h - sh)
            others = [i for i in range(n) if i != feat]
            sw = area.w / len(others)
            return [(feat, main)] + [(o, pygame.Rect(area.x + int(j * sw), main.bottom, int((j + 1) * sw) - int(j * sw), sh))
                                     for j, o in enumerate(others)]
        if ui.layout == "grid" and n >= 3:
            cw, ch = area.w // 2, area.h // 2
            return [(i, pygame.Rect(area.x + (i % 2) * cw, area.y + (i // 2) * ch,
                                    cw if i % 2 == 0 else area.w - cw, ch if i < 2 else area.h - ch)) for i in range(n)]
        lh = area.h / n
        return [(i, pygame.Rect(area.x, area.y + int(i * lh), area.w, int((i + 1) * lh) - int(i * lh))) for i in range(n)]

    def lane_at(self, pos):
        for i, rect in self.last_layout:
            if rect.collidepoint(pos):
                return i
        return None

    @staticmethod
    def timeline_rect():
        return pygame.Rect(px(16), ROW_A + px(9), W - px(32), px(8))

    def frame_at_x(self, x):
        r = self.timeline_rect()
        return int(round(max(0.0, min(1.0, (x - r.x) / r.w)) * (self.n_frames - 1)))

    def split(self, t):
        """回放位置（可为小数帧号）→ (整数帧 k, 帧内小数 fr)；fr 只在两帧之间时非 0。"""
        t = max(0.0, min(float(self.n_frames - 1), float(t)))
        k = int(t)
        return k, (t - k if k < self.n_frames - 1 else 0.0)

    # -- 整帧 --
    def draw(self, surf, t, ui):
        """t 为回放位置（帧号，可带小数）：小数部分在相邻两帧录制位姿/镜头/特效时间之间插值，
        整数位置与不插值时逐字节一致。"""
        if UI_STYLE == "game":
            return gui.draw_frame(self, surf, t, ui)
        k, fr = self.split(t)
        lay = self.layout(k, ui)
        self.last_layout = lay
        surf.fill(BG)
        if ui.layout == "grid" and len(self.lanes) == 3 and ui.focus is None and not ui.follow:
            cw, ch = lay[0][1].w, lay[0][1].h
            self._draw_placeholder(surf, pygame.Rect(lay[0][1].x + cw, TOP + ch, lay[1][1].w, lay[2][1].h))
        for i, rect in lay:
            self._draw_lane(surf, i, rect, k, fr, ui)
        for i, rect in lay:
            pygame.draw.rect(surf, BG, rect, px(2))
        if len(lay) > 1 and (ui.focus is not None or ui.follow):
            i, rect = lay[0]
            pygame.draw.rect(surf, self.colors[i], rect, px(3))
            tag = "焦点赛道 · 再次点击还原" if ui.focus is not None else "追拍 · 当前领先"
            img = text(tag, px(13), (255, 255, 255), True)
            r = pygame.Rect(0, 0, img.get_width() + px(16), img.get_height() + px(4))
            r.bottomright = (rect.right - px(8), rect.bottom - px(6))
            pygame.draw.rect(surf, self.colors[i], r, border_radius=r.h // 2)
            surf.blit(img, (r.x + px(8), r.y + px(2)))
        self._draw_top(surf, k, fr, ui)
        if ui.end_t is not None:
            self._draw_board(surf, ui.end_t)
        if ui.hover is not None:
            self._draw_hover(surf, ui.hover, k)
        if ui.show_help:
            self._draw_help(surf)
        if ui.toast:
            img = text(ui.toast[0], px(16), (255, 255, 255), True)
            r = pygame.Rect(0, 0, img.get_width() + px(28), img.get_height() + px(12))
            r.midbottom = (W // 2, H - px(18))
            surf.blit(fx.panel(r.w, r.h, (20, 24, 32), 220, r.h // 2), r)
            surf.blit(img, (r.x + px(14), r.y + px(6)))

    def _draw_placeholder(self, surf, rect):
        pygame.draw.rect(surf, (34, 40, 54), rect)
        fx.text_shadow(surf, "QWOP AI 运动会", px(30), GOLD, (rect.centerx, rect.centery - px(30)), bold=True, center=True)
        sub = f"{physics.TRACKS[self.track]['label']} · {fx.MODE_LABEL.get(self.mode, self.mode)}"
        fx.text_shadow(surf, sub, px(18), (210, 216, 228), (rect.centerx, rect.centery + px(14)), center=True)

    # -- 单条赛道 --
    def _draw_lane(self, surf, i, rect, k, fr, ui):
        """k/fr：整数帧 + 帧内小数。位姿、镜头、阴影、特效时间用 kf = k + fr 连续取值；
        文字类（HUD 数值、气泡姿态、表情）用整数帧 k。"""
        lane = self.lanes[i]
        lfx = lane["fx"]
        frames = lane["frames"]
        kf = k + fr
        color = self.colors[i]
        scale = _scale_for(rect)
        cx = seq_val(lane["cam"], k, fr)
        cy = 0.5 * physics.terrain_height(cx, self.track)     # 只跟一半坡高，坡顶时远处平地不掉出画面
        ox, oy = lfx.shake(kf, scale) if ui.fx_on else (0.0, 0.0)
        v = View(rect, scale, cx, cy, ox, oy)
        surf.set_clip(rect)
        if not (UI_STYLE == "game" and self.mode == "normal"
                and game_scene.background(surf, rect, cx * scale, ox, oy)):
            surf.blit(fx.sky(rect.w, rect.h, self.mode, color), rect.topleft)
            self._draw_parallax(surf, rect, cx * scale, ox, oy)
        self._draw_ground(surf, v)
        self._draw_lost(surf, v, lane, k, fr)
        pose = lane["poses"][k] if fr == 0 else lerp_pose(lane["poses"][k], lane["poses"][k + 1], fr)
        for idx, verts in self.hurdle_parts:
            fx.draw_hurdle(surf, _poly_screen(v, pose[idx], verts), INK)
        self._draw_shadow(surf, v, frame_val(frames, k, fr, "x"), frame_val(frames, k, fr, "y"))
        if ui.fx_on:
            self._draw_speedlines(surf, v, pose, frame_val(frames, k, fr, "vx"), k, color, i)
        # 视线方向：躯干速度（vy 由相邻两帧躯干高度差分得到），只影响眼珠朝向
        k2 = min(k + 1, self.n_frames - 1)
        vy = (lane["poses"][k2][0][1] - lane["poses"][k2 - 1][0][1]) * FPS / PPM if k2 > 0 else 0.0
        expr = self._expression(lane, k)
        head = self._draw_runner(surf, v, pose, self.run_colors[i], expr, kf,
                                 lane_i=i, look=(frame_val(frames, k, fr, "vx"), vy))
        ch = self.chars[i]
        if ch is not None and ch.head is not None:      # 头像贴在头上（跟着插值后的躯干位姿走），返回头像的圆心/半径
            head = self._draw_avatar(surf, ch, head, pose[0][2], expr, kf)
        if ui.fx_on:
            lfx.draw_dust(surf, v, kf, self.style["dust"])
            lfx.draw_debris(surf, v, kf, lambda x: physics.terrain_height(x, self.track))
            if BODY_STYLE == "robot":
                lfx.draw_screws(surf, v, kf, lambda x: physics.terrain_height(x, self.track))
            lfx.draw_confetti(surf, v, kf)
        self._draw_overhead(surf, v, lane, k, fr, head, color, ui)
        if ui.fx_on:
            self._draw_finish_flash(surf, rect, lane, k, fr)
        if ch is not None:
            self._draw_popup(surf, v, lane, i, k, fr, head, pose, ui)
        if ui.show_hud:
            self._draw_hud(surf, rect, lane, i, k, fr, ui)
        surf.set_clip(None)

    def _draw_parallax(self, surf, rect, cam_px, ox, oy):
        for layer, fac in ((fx.far_layer(rect.h, self.mode), 0.08), (fx.near_layer(rect.h, self.mode), 0.25)):
            x = rect.x - int(cam_px * fac) % fx.TW + int(ox * fac)
            while x < rect.right:
                if x + fx.TW > rect.x:
                    surf.blit(layer, (x, rect.y + int(oy * fac)))
                x += fx.TW

    def _draw_ground(self, surf, v):
        sc, rect = v.scale, v.rect
        yt = max(2.5, math.ceil((0.78 * rect.h / sc + 0.45) * 4) / 4)
        yb = -math.ceil((0.22 * rect.h / sc + 0.3) * 4) / 4
        base = int(round(v.ax + fx.XMIN * sc))
        y = int(round(v.ay - yt * sc))
        i_max = int((fx.XMAX - fx.XMIN) * sc) // fx.CW
        for i in range(max(0, (rect.x - base) // fx.CW), min(i_max, (rect.right - base) // fx.CW) + 1):
            surf.blit(fx.ground_chunk(sc, i, yt, yb, self.mode, self.track), (base + i * fx.CW, y))

    def _draw_lost(self, surf, v, lane, k, fr=0.0):
        """倒地期间被扣掉的那段地面：草皮下方画红色斜纹带（正在扣分的一端随插值位置连续延伸）。"""
        frames = lane["frames"]
        lo, hi = v.x_range()
        th = fx.GRASS_TH
        for _k0, k1, xb, _loss in lane["fx"].runs_until(k):
            xe = frames[k1]["x"] if k >= k1 else frame_val(frames, k, fr, "x")
            a, b = sorted((xb, xe))
            a, b = max(a, lo - 0.2), min(b, hi + 0.2)
            if b - a < 0.01:
                continue
            seg = fx._terrain_pts(a, b, self.track)
            top = [v.w2s(x, y - th) for x, y in seg]
            bot = [v.w2s(x, y - th - 0.12) for x, y in reversed(seg)]
            pygame.draw.polygon(surf, (228, 62, 56), top + bot)
            x = math.ceil(a / 0.08) * 0.08
            while x < b - 0.04:
                y = physics.terrain_height(x, self.track)
                pygame.draw.line(surf, (160, 30, 30), v.w2s(x, y - th), v.w2s(x + 0.05, y - th - 0.12), px(2))
                x += 0.08
            pygame.draw.lines(surf, (200, 30, 30), False, [v.w2s(x, y) for x, y in seg], px(3))

    def _draw_shadow(self, surf, v, x, y):
        hgt = max(0.0, y)
        w = int(v.scale * 0.8 * max(0.35, 1 - 0.3 * max(0.0, hgt - 0.6))) // 4 * 4
        if w < px(8):
            return
        a = int(80 * max(0.3, 1 - 0.35 * max(0.0, hgt - 1.0))) // 10 * 10
        sx, sy = v.w2s(x, physics.terrain_height(x, self.track))
        img = fx.shadow_sprite(w, max(px(3), w // 6), a)
        surf.blit(img, (sx - w // 2, sy - img.get_height() // 2))

    def _draw_speedlines(self, surf, v, pose, vx, k, color, i):
        """速度线：每 3 帧换一组随机线（有意的频闪），位置跟着插值后的躯干走。"""
        if vx < 1.1:
            return
        inten = min(1.0, (vx - 1.1) / 1.2)
        tx, ty = pose[0][0] / PPM, pose[0][1] / PPM
        col = mix((255, 255, 255), color, 0.25)
        for j in range(3 + int(5 * inten)):
            r = self.rtab[((k // 3) * 7 + j * 13 + i * 29) % len(self.rtab)]
            y = ty + (r[0] - 0.5) * 1.2
            x0 = tx - 0.35 - r[1] * 0.5
            ln = (0.25 + r[2] * 0.5) * (0.6 + inten)
            pygame.draw.line(surf, col, v.w2s(x0, y), v.w2s(x0 - ln, y), px(2))

    def _expression(self, lane, k):
        if lane["finish_k"] is not None and k >= lane["finish_k"]:
            return "grin"
        lab = lane["fx"].bubble[k]
        if lab in FALL_LABELS:
            return "dizzy"
        if lab in ("fly", "unbalanced"):
            return "surprised"
        return "normal"

    def _draw_runner(self, surf, v, pose, color, expr, k, lane_i=0, look=None):
        """选手造型全部在 viz_runner.py（胶囊四肢、球衣/短裤/跑鞋、发型/表情）。返回 (头心, 头半径)。
        旧版（纯色矩形 + 白色关节点）备份在 engine/_style_backup/visualizer.py.bak。
        --body-style v2 时改用 viz_body2.draw_runner（同一接口、同一返回值）。"""
        mod = viz_robot if BODY_STYLE == "robot" else viz_body2 if BODY_STYLE == "v2" else viz_runner
        return mod.draw_runner(surf, v, pose, color, expr, k, lane_i=lane_i,
                               label=self.initials[lane_i] if lane_i < len(self.initials) else "", look=look)

    # -- 角色化：头像 / 表情小图标 / Q 版弹窗 / 头像卡（只在 --avatars 时调用）--
    @staticmethod
    def _avatar_geom(head, a):
        """物理头（圆心 c、半径 R、躯干角 a）→ 头像的 (中心, 边长 D)。头像里脸心约在图中心下方 0.17D，
        对齐到物理头心略上方：下沿只到下巴/脖子，不压躯干；随躯干角旋转。"""
        (cx, cy), R = head
        D = max(px(AV_MIN_D), int(round(2 * R * AV_SCALE)))
        up = AV_FACE_Y * D + AV_LIFT * R
        return (cx - math.sin(a) * up, cy - math.cos(a) * up), D

    def _draw_avatar(self, surf, ch, head, a, expr, k):
        """头像贴在头部刚体上（a = 插值后的躯干角，旋转结果按 2° 缓存），原来的表情改成头像旁的小图标。
        返回 (头像中心, 有效半径) 给头顶气泡/弹窗定位。"""
        (ax, ay), D = self._avatar_geom(head, a)
        ow = px(1 if D < pf(56) else 2)
        if BODY_STYLE == "robot":            # 机器人：头像嵌进电视屏幕（不再贴一张大头像盖住电视头）
            viz_robot.draw_screen_avatar(surf, ch, head, a, expr, k)
        else:
            img = ch.head_rot(D, ow, math.degrees(a))
            surf.blit(img, (int(round(ax - img.get_width() / 2)), int(round(ay - img.get_height() / 2))))
        Ra = D / 2
        if expr == "dizzy":                  # 头顶转圈的小星星
            for j in range(3):
                t = k * 0.12 + j * 2.094
                sx, sy = ax + math.cos(t) * Ra * 0.95, ay - Ra * 0.98 + math.sin(t) * Ra * 0.28
                rs = max(pf(2.5), Ra * 0.2)
                star = [(sx + math.cos(q * math.pi / 5 - 1.57) * (rs if q % 2 == 0 else rs * 0.45),
                         sy + math.sin(q * math.pi / 5 - 1.57) * (rs if q % 2 == 0 else rs * 0.45)) for q in range(10)]
                pygame.draw.polygon(surf, (255, 215, 40), star)
                pygame.draw.polygon(surf, (190, 130, 20), star, px(1))
        elif expr == "surprised":            # 右上角红色感叹号 + 左侧汗滴
            r = max(px(5), int(Ra * 0.3))
            ex, ey = ax + Ra * 0.78, ay - Ra * 0.72
            pygame.draw.circle(surf, (255, 255, 255), (ex, ey), r + px(1))
            pygame.draw.circle(surf, DOWN_RED, (ex, ey), r)
            pygame.draw.line(surf, (255, 255, 255), (ex, ey - r * 0.55), (ex, ey + r * 0.1), max(1, r // 3))
            pygame.draw.circle(surf, (255, 255, 255), (ex, ey + r * 0.5), max(1, r // 5))
            dx, dy, rd = ax - Ra * 0.8, ay - Ra * 0.2, max(pf(2.5), Ra * 0.14)
            pygame.draw.circle(surf, (110, 185, 250), (dx, dy + rd * 0.6), rd)
            pygame.draw.polygon(surf, (110, 185, 250), [(dx - rd * 0.9, dy + rd * 0.4), (dx + rd * 0.9, dy + rd * 0.4), (dx, dy - rd * 1.3)])
        elif expr == "grin":                 # 冲线后：两颗一闪一闪的金色十字星
            for j, (px_, py_) in enumerate(((0.85, -0.7), (-0.82, -0.55))):
                s = max(pf(3.0), Ra * 0.24) * (0.75 + 0.25 * math.sin(k * 0.25 + j * 2.0))
                cx, cy = ax + px_ * Ra, ay + py_ * Ra
                pts = [(cx, cy - s), (cx + s * 0.28, cy - s * 0.28), (cx + s, cy), (cx + s * 0.28, cy + s * 0.28),
                       (cx, cy + s), (cx - s * 0.28, cy + s * 0.28), (cx - s, cy), (cx - s * 0.28, cy - s * 0.28)]
                pygame.draw.polygon(surf, GOLD, pts)
                pygame.draw.polygon(surf, (200, 140, 20), pts, px(1))
        return (ax, ay), Ra * 0.92

    def _runner_box(self, v, pose, head):
        """小人（含头像）在屏幕上的包围盒，弹窗避让用。"""
        pts = [v.w2s(p[0] / PPM, p[1] / PPM) for p in pose[:11]]
        (hx, hy), hr = head
        xs = [p[0] for p in pts] + [hx - hr, hx + hr]
        ys = [p[1] for p in pts] + [hy - hr, hy + hr]
        m = v.scale * 0.14
        return pygame.Rect(int(min(xs) - m), int(min(ys) - m), int(max(xs) - min(xs) + 2 * m), int(max(ys) - min(ys) + 2 * m))

    def _popup_place(self, v, lane, i, pose, head, w, h, ui):
        """在 头的左上 / 右上 / 头顶 三个位置里挑一个最不挡东西的（小人本体、选手卡、关节波形、头顶气泡区、路牌高度以下），
        返回 (锚点类型, 锚点相对头像中心的偏移)。只在弹窗出现的那一帧算一次（调用方缓存），之后跟着头走。"""
        rect = v.rect
        (hx, hy), hr = head
        body = self._runner_box(v, pose, head)
        x_c = pose[0][0] / PPM
        gy = v.w2s(x_c, physics.terrain_height(x_c, self.track))[1]
        obst = [body, pygame.Rect(int(hx - hr * 2.2), int(hy - hr - px(60)), int(hr * 4.4), px(60))]        # 头顶气泡/扣分标签区
        sfs = max(px(10), min(px(18), int(v.scale * 0.13)))                   # 区域路牌（与 viz_fx._paint_ground 的几何一致）
        lo, hi = v.x_range()
        for zx, zn in physics.TRACKS[self.track]["zones"]:
            sx = zx + 0.4
            if zx < physics.TRACK_LENGTH and lo - 1 <= sx <= hi + 1:
                tx, ty = v.w2s(sx, physics.terrain_height(sx, self.track) + 0.42)
                bw = text(zn, sfs, INK, True).get_width() + px(16)
                obst.append(pygame.Rect(int(tx - bw / 2), int(ty - sfs * 1.5 - px(8)), int(bw), int(sfs * 1.5 + px(8) + 0.42 * v.scale)))
        if ui.show_hud and self._card_tier(rect) != "strip" and UI_STYLE == "game":
            obst += gui.hud_obstacles(self, rect, lane, i, ui)
        elif ui.show_hud and self._card_tier(rect) != "strip":
            obst.append(self._avatar_card(None, rect, lane, i, lane["frames"][0], self._card_tier(rect)).inflate(px(8), px(8)))
            wr = self._wave_rect(rect)
            if wr:
                obst.append(wr.inflate(px(8), px(8)))
        gap = px(6)
        y_head, y_low = hy + hr * 0.4, gy - 0.12 * v.scale
        cands = [("br", body.left - gap, y_head), ("bl", body.right + gap, y_head),      # 头的左上 / 右上
                 ("bc", hx, hy - hr - px(8)),                                           # 头顶
                 ("br", body.left - gap, y_low), ("bl", body.right + gap, y_low)]       # 身体左右两侧（贴近地面）
        inner = rect.inflate(-px(8), -px(8))
        best = None
        for pref, (kind, qx, qy) in enumerate(cands):
            r = self._popup_rect(kind, qx, qy, w, h)
            rc = r.clamp(inner) if r.w < inner.w and r.h < inner.h else r
            cost = sum(_overlap(rc, o) for o in obst) + 2 * (abs(rc.x - r.x) + abs(rc.y - r.y)) * h
            cost += max(0, rc.bottom - y_low) * w * 3 + pref * pf(pf(60))
            if best is None or cost < best[0]:
                ax_, ay_ = {"br": rc.bottomright, "bl": rc.bottomleft, "bc": rc.midbottom}[kind]
                best = (cost, kind, ax_ - hx, ay_ - gy)
        return best[1], (best[2], best[3])       # 水平相对头像中心，竖直相对脚下地面（摔倒时不跟着头往下掉）

    @staticmethod
    def _popup_rect(kind, px, py, w, h):
        r = pygame.Rect(0, 0, int(w), int(h))
        if kind == "br":
            r.bottomright = (int(px), int(py))
        elif kind == "bl":
            r.bottomleft = (int(px), int(py))
        else:
            r.midbottom = (int(px), int(py))
        return r

    def _draw_popup(self, surf, v, lane, i, k, fr, head, pose, ui):
        """Q 版表情弹窗（事件由 fx.schedule_popups 预先排好；结束画面按名次弹 得意/苦涩）。
        位置跟着头走（插值），弹出/淡出动画按 kf 连续计时；位置选择在弹出那一帧确定（可回退/拖动）。"""
        ch = self.chars[i]
        rect = v.rect
        kf = k + fr
        last = self.n_frames - 1
        ev = None
        if ui.end_t is not None and k >= last and len(self.lanes) >= 2:
            rk = self.ranked()
            name = "end_first" if rk[0] == i else "end_last" if rk[-1] == i else None
            if name:
                t = ui.end_t - 0.6
                ev = (name, fx.POPUP_RULES[name]["expr"], t, None, ("end", i), last)
        if ev is None:
            j = lane["popup_at"][k]
            if j < 0:
                return
            k0, k1, name, expr = lane["popups"][j]
            ev = (name, expr, (kf - k0) / FPS, (k1 - kf) / FPS if k1 < self.n_frames else None, (j, i), k0)
        name, expr, t, t_left, ckey, kref = ev
        if t < 0:
            return
        hq = max(px(44), min(px(170), int(rect.h * 0.47)))
        img = ch.q_scaled(expr, hq, fx.POPUP_RULES[name]["fallback"], ow=px(2 if hq >= pf(80) else 1))
        if img is None:
            return
        key = (ckey, rect.w, rect.h, ui.show_hud)
        place = self._popup_side.get(key)
        if place is None:                    # 在弹出那一帧（整数帧、无震屏）的镜头/位姿下选位置
            cx0 = lane["cam"][kref]
            v0 = View(rect, v.scale, cx0, 0.5 * physics.terrain_height(cx0, self.track))
            p0 = lane["poses"][kref]
            from_head = self._avatar_head_at(v0, p0, i)
            place = self._popup_side[key] = self._popup_place(v0, lane, i, p0, from_head, img.get_width(), img.get_height(), ui)
        kind, (dx, dy) = place
        s, alpha, lift = fx.popup_anim(t, t_left, ui.fx_on)
        (hx, _hy), _hr = head
        x_c = pose[0][0] / PPM
        gy = v.w2s(x_c, physics.terrain_height(x_c, self.track))[1]
        w, h = img.get_size()
        if s != 1.0:
            img = pygame.transform.smoothscale(img, (max(2, int(w * s)), max(2, int(h * s))))
        full = self._popup_rect(kind, hx + dx, gy + dy - lift * h, w, h)
        p8 = px(8)
        full = full.clamp(rect.inflate(-p8, -p8)) if w < rect.w - p8 and h < rect.h - p8 else full
        # 以靠近头的那个角为支点缩放（像从头边上 "啵" 地冒出来）
        piv = {"br": full.bottomright, "bl": full.bottomleft, "bc": full.midbottom}[kind]
        r = self._popup_rect(kind, piv[0], piv[1], img.get_width(), img.get_height())
        if alpha < 255:
            img = img.copy()
            img.set_alpha(alpha)
        surf.blit(img, r.topleft)

    def _avatar_head_at(self, v, pose, i):
        """不画图，只算某一帧头像的 (中心, 有效半径)（与 _draw_avatar 的几何一致）。"""
        px, py, a = pose[0]
        ly = physics.TORSO_H / 2 + physics.HEAD_R
        c = v.w2s((px - ly * math.sin(a)) / PPM, (py + ly * math.cos(a)) / PPM)
        (ax, ay), D = self._avatar_geom((c, physics.HEAD_R * v.scale / PPM), a)
        return (ax, ay), D / 2 * 0.92

    @staticmethod
    def _card_tier(rect):
        return "strip" if rect.h < pf(120) or rect.w < pf(380) else "full" if rect.h >= pf(220) else "mid"

    def _avatar_card(self, surf, rect, lane, i, f, tier):
        """头像信息卡（替代左上角选手卡）：圆形头像（主题色描边 + 赛道号小徽章）| 名字 / 模型 · 强度 / 成绩 + 实际 | 小号触地图。
        surf=None 时只量尺寸（弹窗避让用）。返回卡片矩形。"""
        ch, color = self.chars[i], self.colors[i]
        full = tier == "full"
        p1, p2, p4, p5, p8 = px(1), px(2), px(4), px(5), px(8)
        P = px(62) if full else px(48)
        fs_name = px(20) if full else px(16)
        fs_model = px(13) if full else px(12)
        fs_sc = px(22) if full else px(18)
        pad = px(6)
        x0, y0 = rect.x + px(6), rect.y + px(6)
        name_img_h = text("名", fs_name, INK, True).get_height()
        eff = hud.cpill(lane["effort"], fs_model - p1, (255, 255, 255), shade(color, 0.85)) if lane.get("effort") else None
        lab = text("成绩", fs_model, hud.MUTED, True)
        sc_img = text(f"{f['score']:.2f}", fs_sc, INK, True)
        unit = text("m", fs_model, INK, True)
        diff = abs(f["x"] - f["score"]) >= 0.005
        act = text(f"实际 {f['x']:.2f}", fs_model - p1, (200, 70, 60), True) if diff else None
        u = pf(5.0) if full else pf(4.2)                          # 触地小人图（缩小）
        picto_w = int(u * 2.6) + p4
        max_w = int(max(px(230), min(px(340), rect.w * 0.44)))
        tx = x0 + pad + P + p8
        text_max = x0 + max_w - pad - picto_w - px(6) - tx
        name_img = hud.fit(lane["name"], text_max, fs_name, INK, True, min_size=max(px(12), fs_name - p5))
        model_img = None
        if lane["model"]:
            model_img = hud.fit(lane["model"], text_max - (eff.get_width() + p5 if eff else 0), fs_model, hud.SUB, False, min_size=fs_model - p1)
        row2 = max(model_img.get_height() if model_img else 0, eff.get_height() if eff else 0)
        score_w = lab.get_width() + p4 + sc_img.get_width() + p2 + unit.get_width() + (p8 + act.get_width() if act else 0)
        mw = (model_img.get_width() if model_img else 0) + (eff.get_width() + (p5 if model_img else 0) if eff else 0)
        want = max(name_img.get_width(), mw, score_w)
        cw = max(px(200) if full else px(186), min(max_w, tx - x0 + want + p8 + picto_w + pad))
        act_row = 0                    # "实际" 放不进成绩那一行时单独占一行（倒地扣分信息不能丢）
        if act and score_w > cw - (tx - x0) - picto_w - pad - p4:
            act_row = act.get_height() - p2
        text_h = name_img_h - p2 + (row2 if row2 else 0) + sc_img.get_height() - p4 + act_row
        chh = max(P + 2 * pad, text_h + 2 * pad)
        card = pygame.Rect(x0, y0, cw, chh)
        if surf is None:
            return card
        surf.blit(fx.panel(cw, chh, (255, 255, 255), 222, px(12)), card.topleft)
        pygame.draw.rect(surf, color, (card.x, card.y + p8, p4, chh - px(16)), border_radius=p2)
        py_ = card.y + (chh - P) // 2
        surf.blit(ch.portrait(P, color, px(3) if full else p2), (x0 + pad, py_))
        br = p8 if full else px(7)
        hud.lane_badge(surf, (x0 + pad + br - p1, py_ + P - br + p1), br, i + 1, color)
        yy = card.y + (chh - text_h) // 2 - p1
        surf.blit(name_img, (tx, yy))
        yy += name_img_h - p2
        if row2:
            mx = tx
            if model_img:
                surf.blit(model_img, (mx, yy + (row2 - model_img.get_height()) // 2))
                mx += model_img.get_width() + p5
            if eff:
                surf.blit(eff, (mx, yy + (row2 - eff.get_height()) // 2))
            yy += row2
        base = yy + sc_img.get_height() - p5
        surf.blit(lab, (tx, base - lab.get_height() + p1))
        sx = tx + lab.get_width() + p4
        surf.blit(sc_img, (sx, base - sc_img.get_height() + p4))
        sx += sc_img.get_width() + p2
        surf.blit(unit, (sx, base - unit.get_height() + p1))
        sx += unit.get_width() + p8
        if act and not act_row:
            surf.blit(act, (sx, base - act.get_height()))
        elif act:
            surf.blit(act, (tx, base + p1))
        hud.contact_picto(surf, card.right - pad - picto_w // 2, card.y + (chh - u * 2.4) / 2 - p1, u, f["contact"])
        return card

    def _loss_pill(self, amount, fs):
        img = text(f"{-amount:+.2f} m 倒地不计分", fs, (255, 255, 255), True)
        s = pygame.Surface((img.get_width() + px(14), img.get_height() + px(4)), pygame.SRCALPHA)
        pygame.draw.rect(s, DOWN_RED, s.get_rect(), border_radius=s.get_height() // 2)
        pygame.draw.rect(s, (255, 255, 255), s.get_rect(), px(1), border_radius=s.get_height() // 2)
        s.blit(img, (px(7), px(2)))
        return s

    def _draw_overhead(self, surf, v, lane, k, fr, head, color, ui):
        """头顶：实时扣分标签、扣分飘字、姿态气泡（会被夹在赛道矩形内）。
        文字取整数帧 k；位置跟头走（已插值），飘字/弹出动画按 kf 连续计时。"""
        if UI_STYLE == "game":
            return gui.draw_overhead(self, v, surf, lane, k, fr, head, color, ui)
        rect, frames, lfx = v.rect, lane["frames"], lane["fx"]
        kf = k + fr
        c, R = head
        fs = max(px(11), min(px(17), int(v.scale * 0.11)))
        p2, p3, p4 = px(2), px(3), px(4)
        stack = []                                   # 头顶要叠放的贴图（自下而上）
        runs = lfx.runs_until(k)
        live = False
        if runs and k <= runs[-1][1]:
            cur = frames[k]["x"] - runs[-1][2]
            if abs(cur) >= 0.05:
                stack.append(self._loss_pill(cur, fs))
                live = True
        j = lfx.bubble_at[k]
        if j >= 0:
            k0, k1, key = lfx.bubble_events[j]
            bfs = max(px(12), min(px(19), int(v.scale * 0.13)))
            t, t_end = (kf - k0) / FPS, (k1 - kf) / FPS
            s, alpha = 1.0, 255
            if ui.fx_on and k0 > 0 and t < 0.3:          # 冒出时的弹出动画
                s = 0.55 + 0.57 * t / 0.12 if t < 0.12 else 1.12 - 0.12 * (t - 0.12) / 0.18
            elif ui.fx_on and lfx.bubble_mode == "few" and k1 < self.n_frames - 1 and t_end < 0.25:   # 收起
                s, alpha = 0.7 + 0.3 * max(0.0, t_end) / 0.25, int(255 * max(0.0, t_end) / 0.25)
            stack.append((key, bfs, s, alpha))
        # 头顶放得下就叠在头顶；放不下（叠放布局里人靠近上沿）就挂到头的右侧，不压脸
        gap = p3
        sizes = [(fx.bubble_sprite(it[0], it[1], color) if isinstance(it, tuple) else it).get_size() for it in stack]
        tot = sum(h + gap for _w, h in sizes)
        side = None
        if c[1] - R - p3 - tot < rect.y + p2:
            maxw = max((w for w, _h in sizes), default=0) + px(8)
            y = max(rect.y + p2, min(rect.bottom - tot - p2, c[1] - tot / 2))
            right = pygame.Rect(int(c[0] + R + p4), int(y), maxw, int(tot))
            wave = self._wave_rect(rect) if ui.show_hud else None
            side = "right"
            if right.right > rect.right - p2 or (wave and right.colliderect(wave)):
                side = "left"                       # 右边是关节图/出界：挂到头的左边
        else:
            y = c[1] - R - p3
        for it in (reversed(stack) if side else stack):
            if isinstance(it, tuple):
                key, bfs, s, alpha = it
                img = fx.bubble_sprite(key, bfs, color, {"right": "left", "left": "right"}.get(side, "down"))
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
                bx = min(rect.right - full_w - p2, c[0] + R + p4)
                surf.blit(img, (bx, y + (full_h - img.get_height()) / 2))
                y += full_h + gap
            elif side == "left":
                bx = max(rect.x + p2, c[0] - R - p4 - full_w)
                surf.blit(img, (bx + full_w - img.get_width(), y + (full_h - img.get_height()) / 2))
                y += full_h + gap
            else:
                bx = max(rect.x + p2, min(rect.right - img.get_width() - p2, c[0] - img.get_width() / 2))
                surf.blit(img, (bx, y - full_h + (full_h - img.get_height())))
                y -= full_h + gap
        if live:                                     # 正在扣分时不再叠加上一段的飘字
            return
        for _k0, k1, _xb, loss in reversed(runs):        # 一段倒地结束后，扣掉的距离向上飘走
            if k1 + 96 < k:
                break
            if k1 < k and abs(loss) >= 0.05:
                t = (kf - k1) / 96
                pb = lane["poses"][k1][0]
                sx, sy = v.w2s(pb[0] / PPM, pb[1] / PPM + 0.9 + 0.7 * t)
                pill = self._loss_pill(loss, fs)
                pill.set_alpha(int(255 * (1 - t ** 2)))
                surf.blit(pill, (sx - pill.get_width() / 2, max(rect.y + p2, sy)))
                break

    def _draw_finish_flash(self, surf, rect, lane, k, fr=0.0):
        if UI_STYLE == "game":
            return gui.draw_finish(self, surf, rect, lane, next(j for j, L in enumerate(self.lanes) if L is lane), k, fr)
        fk = lane["finish_k"]
        if fk is None or not (fk <= k < fk + 150):
            return
        t = (k + fr - fk) / FPS
        if t < 0.35:
            a = int(140 * (1 - t / 0.35)) // 10 * 10
            if a > 0:
                surf.blit(fx.panel(rect.w, rect.h, (255, 255, 255), a, 0), rect.topleft)
        s = 0.5 + 0.6 * t / 0.15 if t < 0.15 else 1.1 - 0.1 * min(1.0, (t - 0.15) / 0.2)
        fs = max(px(18), min(px(40), int(rect.h * 0.11)))
        img = text(f"冲线！{lane['result']['finish_time']:.2f}s", fs, (120, 40, 0), True)
        bw, bh = img.get_width() + px(36), img.get_height() + px(14)
        ban = pygame.Surface((bw, bh), pygame.SRCALPHA)
        pygame.draw.rect(ban, GOLD, ban.get_rect(), border_radius=bh // 2)
        pygame.draw.rect(ban, (255, 255, 255), ban.get_rect(), px(3), border_radius=bh // 2)
        ban.blit(img, (px(18), px(7)))
        if s != 1.0:
            ban = pygame.transform.smoothscale(ban, (int(bw * s), int(bh * s)))
        if t > 2.0:
            ban.set_alpha(int(255 * max(0.0, 1 - (t - 2.0) / 0.5)))
        cx = rect.x + rect.w * min(0.74, _anchor(rect) + 0.3)       # 放在小人前方的天空里，不挡脸
        surf.blit(ban, (int(cx - ban.get_width() / 2), rect.y + int(rect.h * 0.45) - ban.get_height() // 2))

    # -- HUD --
    def _draw_hud(self, surf, rect, lane, i, k, fr, ui):
        """左上角选手卡：赛道号徽章 + 选手名（大）/ 模型 · 推理强度（小）/ 成绩（实际距离不同时才显示）+ 触地小人图。
        极小的赛道（追拍小窗）只在底部画一行。"""
        if UI_STYLE == "game":
            return gui.draw_hud(self, surf, rect, lane, i, k, fr, ui)
        f = lane["frames"][k]
        color = self.colors[i]
        ch = self.chars[i]
        if rect.h < pf(120) or rect.w < pf(380):
            card = hud.strip_card(surf, rect, i + 1, color, lane["name"], f["score"], max(px(12), min(px(15), int(rect.h * 0.12))))
            if ch is not None and ch.head is not None:      # 小窗：赛道号徽章换成圆形头像
                d = card.h - px(2)
                surf.blit(ch.portrait(d, color, px(2)), (card.x + px(2), card.y + px(1)))
            tag_fs = px(12)
        elif ch is not None and ch.head is not None:
            tier = self._card_tier(rect)
            card = self._avatar_card(surf, rect, lane, i, f, tier)
            tag_fs = px(14) if tier == "full" else px(12)
        else:
            tier = "full" if rect.h >= pf(220) else "mid"
            card = hud.lane_card(surf, rect, i + 1, color, lane["name"], lane["model"], lane.get("effort", ""),
                                 f["score"], f["x"], f["contact"], tier)
            tag_fs = px(14) if tier == "full" else px(12)
        tags = []
        if lane["down"][k]:
            tags.append(("倒地 · 不计分", DOWN_RED))
        if lane["finish_k"] is not None and k >= lane["finish_k"]:
            tags.append((f"已冲线 {lane['result']['finish_time']:.2f}s", (205, 140, 0)))
        if lane["load_error"]:
            tags.append((f"载入失败：{lane['load_error'][:40]}", DOWN_RED))
        elif lane["result"]["errors"] and k >= lane["result"]["errors"][0][0] * FPS:
            tags.append(("act() 报错，沿用上一帧动作", DOWN_RED))
        ty = card.y + px(4)
        for s_, col in tags:                     # 状态标签挂在卡片右侧
            img = hud.status_tag(s_, tag_fs, col)
            surf.blit(img, (card.right + px(6), ty))
            ty += img.get_height() + px(4)
        if rect.w >= pf(600) and rect.h >= pf(140):
            self._draw_wave(surf, rect, lane, k, fr, ui)

    @staticmethod
    def _wave_rect(rect):
        """关节波形面板（含图例条）的位置；没有面板时返回 None。与 _draw_wave 的几何一致。"""
        if rect.w < pf(600) or rect.h < pf(140):
            return None
        ww = px(260) if rect.w >= pf(1000) else px(190)
        return pygame.Rect(rect.right - ww - px(8), rect.y + px(6), ww, max(px(46), min(px(96), int(rect.h * 0.28))) + px(15))

    def _draw_wave(self, surf, rect, lane, k, fr, ui):
        """关节波形：采样点固定在整数帧上，横坐标按 kf 连续平移；帧间时最右端补一个插值点。"""
        js = range(6) if ui.all_joints else (0, 2, 4, 5)
        p1, p2, p4 = px(1), px(2), px(4)
        ww = px(260) if rect.w >= pf(1000) else px(190)
        lh = px(15)                               # 图例条高度（在面板内底部）
        wh = max(px(46), min(px(96), int(rect.h * 0.28))) + lh
        wx, wy = rect.right - ww - px(8), rect.y + px(6)
        surf.blit(fx.panel(ww, wh, (255, 255, 255), 214, px(8)), (wx, wy))
        wh -= lh
        pygame.draw.line(surf, (215, 218, 224), (wx + p4, wy + wh // 2), (wx + ww - p4, wy + wh // 2), p1)
        n = int(WAVE_SECONDS * FPS)
        frames = lane["frames"]
        ks = list(range(k, max(0, k - n) - 1, -2))
        kf = k + fr
        for j in js:
            pts = [(wx + ww - p2 - (kf - kk) * (ww - p4) / n, wy + wh / 2 - frames[kk]["joints"][j] * (wh / 2 - p4)) for kk in ks]
            if fr:
                a = frames[k]["joints"][j]
                pts.insert(0, (wx + ww - p2, wy + wh / 2 - (a + (frames[k + 1]["joints"][j] - a) * fr) * (wh / 2 - p4)))
            if len(pts) > 1:
                pygame.draw.lines(surf, JOINT_COLORS[j], False, pts, p2)
        lx = wx + px(6)
        ly = wy + wh + lh // 2
        pygame.draw.line(surf, (225, 228, 234), (wx + p4, wy + wh), (wx + ww - p4, wy + wh), p1)
        for j in js:                              # 图例：色块短线 + 关节名
            img = text(JOINT_SHORT[j], px(11), shade(JOINT_COLORS[j], 0.75), True)
            pygame.draw.line(surf, JOINT_COLORS[j], (lx, ly), (lx + px(8), ly), px(3))
            surf.blit(img, (lx + px(10), ly - img.get_height() // 2))
            lx += img.get_width() + px(18)
        tail = "关节角 · 3 秒" + (" · J 全部" if not ui.all_joints and ui.interactive else "")
        img = text(tail, px(10), (140, 146, 160))
        if lx + img.get_width() < wx + ww - px(6):
            surf.blit(img, (wx + ww - px(6) - img.get_width(), ly - img.get_height() // 2))

    # -- 顶栏 --
    def _badge(self, surf, x, y, s, col, fg=(255, 255, 255)):
        img = text(s, px(13), fg, True)
        r = pygame.Rect(x, y, img.get_width() + px(12), img.get_height() + px(2))
        pygame.draw.rect(surf, col, r, border_radius=r.h // 2)
        surf.blit(img, (x + px(6), y + px(1)))
        return r.right + px(6)

    def _draw_top(self, surf, k, fr, ui):
        pygame.draw.rect(surf, (20, 24, 32), (0, 0, W, TOP))
        title = f"{physics.TRACKS[self.track]['label']} · {fx.MODE_LABEL.get(self.mode, self.mode)}"
        surf.blit(text(title, px(18), (240, 242, 248), True), (px(14), px(5)))
        st = text(f"{k / FPS:5.2f}s / {self.sim_time:g}s", px(15), (205, 210, 222))
        surf.blit(st, (px(14), px(33)))
        x = px(22) + st.get_width()
        by = px(35)
        x = self._badge(surf, x, by, f"{round(ui.speed, 2):g}x", (70, 80, 100))     # 渐变中的倍速取两位小数
        if ui.paused:
            x = self._badge(surf, x, by, "暂停", (190, 60, 60))
        if ui.slow_active:
            x = self._badge(surf, x, by, "慢动作", (130, 80, 200))
        if ui.follow:
            x = self._badge(surf, x, by, "追拍", (40, 140, 120))
        if ui.show_hud and ui.interactive:
            surf.blit(text("H 帮助", px(12), (120, 128, 145)), (x + px(2), px(37)))
        self._draw_progress(surf, k, fr)
        self._draw_timeline(surf, k + fr, ui)

    def _draw_progress(self, surf, k, fr=0.0):
        mx0, mx1, my = px(430), W - px(46), px(46)
        p1, p2, p3, p5 = px(1), px(2), px(3), px(5)
        span = physics.TRACK_LENGTH
        zones = physics.TRACKS[self.track]["zones"]
        for (za, zn), (zb, _n) in zip(zones, zones[1:]):
            a, b = mx0 + (mx1 - mx0) * za / span, mx0 + (mx1 - mx0) * zb / span
            pygame.draw.line(surf, mix(fx.ZONE_COLORS.get(zn, (120, 120, 120)), (20, 24, 32), 0.35), (a, my), (b, my), p5)
        for m in range(0, int(span) + 1, 2):
            x = mx0 + (mx1 - mx0) * m / span
            pygame.draw.line(surf, (110, 118, 132), (x, my + p3), (x, my + px(6)), p1)
            img = text(str(m), px(10), (150, 156, 170))
            surf.blit(img, (x - img.get_width() // 2, my + px(6)))
        pygame.draw.line(surf, (220, 220, 225), (mx1, my), (mx1, my - px(30)), p2)       # 终点旗
        for j in range(3):
            for row in range(2):
                pygame.draw.rect(surf, (20, 20, 20) if (j + row) % 2 else (250, 250, 250), (mx1 + p1 + j * p5, my - px(30) + row * p5, p5, p5))
        # 选手标记：赛道号圆牌（与选手卡徽章一致）。真实位置在轨道上画彩色刻度；
        # 靠得太近的圆牌并成一排（按位置从左到右），不再上下叠罗汉
        marks = sorted(((mx0 + (mx1 - mx0) * max(-0.6, min(span + 0.6, frame_val(L["frames"], k, fr, "x"))) / span, i)
                        for i, L in enumerate(self.lanes)))
        rr, step = px(8), px(18)
        clusters = []
        for x, i in marks:
            if clusters and x - clusters[-1][-1][0] < step:
                clusters[-1].append((x, i))
            else:
                clusters.append([(x, i)])
        for x, i in marks:
            pygame.draw.line(surf, (255, 255, 255), (x, my - p5), (x, my + p3), px(4))
            pygame.draw.line(surf, self.colors[i], (x, my - px(4)), (x, my + p2), p2)
        prev_right = -1e9
        for cl in clusters:
            cx = sum(x for x, _i in cl) / len(cl)
            w = len(cl) * step
            left = max(mx0 - px(10), min(mx1 + px(6) - w, cx - w / 2), prev_right + p2)
            prev_right = left + w
            y = my - px(15)
            for j, (x, i) in enumerate(cl):
                bx = int(left + step / 2 + j * step)
                if abs(bx - x) > p3:
                    pygame.draw.line(surf, self.colors[i], (bx, y + rr - p1), (x, my - p5), p1)
                ch = self.chars[i]
                if ch is not None and ch.head is not None:      # 角色化：进度条上用小圆头像
                    img = ch.portrait(2 * rr + p2, self.colors[i], p2)
                    surf.blit(img, (bx - rr - p1, y - rr - p1))
                else:
                    hud.lane_badge(surf, (bx, y), rr, i + 1, self.colors[i])

    def _draw_timeline(self, surf, k, ui):
        r = self.timeline_rect()
        p1, p2, p3, p4 = px(1), px(2), px(3), px(4)
        pygame.draw.rect(surf, (55, 62, 76), r, border_radius=p4)
        frac = k / max(1, self.n_frames - 1)
        pygame.draw.rect(surf, (230, 190, 70), (r.x, r.y, max(p4, int(r.w * frac)), r.h), border_radius=p4)
        for a, b in self.slow_windows:
            xa = r.x + r.w * max(0, a) / (self.n_frames - 1)
            xb = r.x + r.w * min(self.n_frames - 1, b) / (self.n_frames - 1)
            pygame.draw.line(surf, (150, 110, 220), (xa, r.bottom + p2), (xb, r.bottom + p2), p2)
        for ke, i, kind in self.events:
            x = r.x + r.w * ke / (self.n_frames - 1)
            y = r.y - p3
            col = self.colors[i]
            if kind == "fall":
                pygame.draw.polygon(surf, col, [(x - p4, y - px(6)), (x + p4, y - px(6)), (x, y)])
                pygame.draw.polygon(surf, (255, 255, 255), [(x - p4, y - px(6)), (x + p4, y - px(6)), (x, y)], p1)
            elif kind == "hurdle":
                pygame.draw.polygon(surf, (250, 150, 40), [(x, y - px(7)), (x + p4, y - p3), (x, y + p1), (x - p4, y - p3)])
            else:
                pygame.draw.line(surf, (255, 255, 255), (x, y + p1), (x, y - px(9)), p1)
                pygame.draw.rect(surf, GOLD, (x, y - px(9), px(7), px(5)))
        hx = r.x + r.w * frac
        pygame.draw.circle(surf, (255, 255, 255), (hx, r.centery), px(7))
        pygame.draw.circle(surf, (230, 190, 70), (hx, r.centery), px(7), p2)

    # -- 结束成绩榜（右侧面板，赛道区让出位置）--
    def ranked(self):
        return sorted(range(len(self.lanes)), key=lambda i: (self.lanes[i]["result"]["finish_time"] is None,
                                                             self.lanes[i]["result"]["finish_time"] or 0,
                                                             -self.lanes[i]["result"]["score"]))

    def _draw_board(self, surf, end_t):
        x0 = W - PANEL_W
        bg = fx._cached(("board_bg", PANEL_W, H - TOP), lambda: _gradient(PANEL_W, H - TOP, (30, 36, 56), (16, 18, 28)))
        surf.blit(bg, (x0, TOP))
        pygame.draw.line(surf, GOLD, (x0, TOP), (x0, H), px(3))
        fx.text_shadow(surf, "比赛结束 · 成绩榜", px(24), GOLD, (x0 + PANEL_W // 2, TOP + px(12)), bold=True, center=True)
        sub = f"{physics.TRACKS[self.track]['label']} · {fx.MODE_LABEL.get(self.mode, self.mode)} · {self.sim_time:g} 秒"
        img = text(sub, px(13), (170, 178, 196))
        surf.blit(img, (x0 + (PANEL_W - img.get_width()) // 2, TOP + px(46)))
        n = len(self.lanes)
        avail = H - TOP - px(84)
        row_h = min(px(124), avail // n)
        y_start = TOP + px(72) + (avail - row_h * n) // 3          # 卡片组略偏上居中（单人时不再贴顶留一大片空）
        p2, p3, p4, p6, p7, p8, p12 = px(2), px(3), px(4), px(6), px(7), px(8), px(12)
        for r, i in enumerate(self.ranked()):
            lane = self.lanes[i]
            res = lane["result"]
            ts = _ease((end_t - 0.15 * r) / 0.4)
            if ts <= 0:
                continue
            tc = _ease((end_t - 0.3 - 0.15 * r) / 1.2)
            card = pygame.Rect(x0 + px(14) + int((1 - ts) * PANEL_W), y_start + r * row_h, PANEL_W - px(28), row_h - px(10))
            pygame.draw.rect(surf, (40, 46, 64), card, border_radius=px(10))
            pygame.draw.rect(surf, mix(self.colors[i], (40, 46, 64), 0.55), card, px(1), border_radius=px(10))
            pygame.draw.rect(surf, self.colors[i], (card.x, card.y, p6, card.h), border_top_left_radius=px(10), border_bottom_left_radius=px(10))
            # 奖牌：彩带 + 圆牌 + 名次
            mc = (card.x + px(36), card.y + px(34))
            col = MEDALS[r] if r < 3 else (110, 118, 135)
            pygame.draw.polygon(surf, self.colors[i], [(mc[0] - p12, card.y + p4), (mc[0] - p3, card.y + p4), (mc[0] + p2, mc[1]), (mc[0] - p7, mc[1])])
            pygame.draw.polygon(surf, shade(self.colors[i], 0.7), [(mc[0] + p12, card.y + p4), (mc[0] + p3, card.y + p4), (mc[0] - p2, mc[1]), (mc[0] + p7, mc[1])])
            pygame.draw.circle(surf, shade(col, 0.7), mc, px(19))
            pygame.draw.circle(surf, col, mc, px(16))
            num = text(str(r + 1), px(20), (60, 40, 10) if r < 3 else (240, 240, 245), True)
            surf.blit(num, (mc[0] - num.get_width() // 2, mc[1] - num.get_height() // 2))
            # 右上：成绩 + 评级
            sc = text(f"{res['score'] * tc:.2f}m", px(28), GOLD, True)
            surf.blit(sc, (card.right - sc.get_width() - p12, card.y + p4))
            grade_txt = next(t for th, g, t in evaluator.GRADES if g == lane["grade"])
            g = hud.cpill(f"{lane['grade']} {grade_txt}", px(13), (30, 30, 40), mix(self.colors[i], (255, 255, 255), 0.5))
            surf.blit(g, (card.right - p12 - g.get_width(), card.y + px(42)))
            # 左：选手名 + 模型 · 强度（缩字号 / 省略号，不越界）
            tx = card.x + px(64)
            ch = self.chars[i]
            if ch is not None and ch.head is not None:      # 角色化：名字前加圆形头像
                pd = min(px(44), card.h - px(16))
                surf.blit(ch.portrait(pd, self.colors[i], p2), (tx, card.y + p7))
                tx += pd + p8
            name_w = card.right - p12 - sc.get_width() - px(10) - tx
            nm = hud.fit(lane["name"], name_w, px(20), (245, 247, 252), True, min_size=px(15))
            surf.blit(nm, (tx, card.y + p8))
            yy = card.y + px(10) + max(nm.get_height(), px(26))
            ml_w = card.right - p12 - g.get_width() - px(10) - tx
            ep = hud.cpill(lane["effort"], px(12), (255, 255, 255), shade(self.colors[i], 0.8)) if lane.get("effort") else None
            mx = tx
            mh = 0
            if lane["model"]:
                mi = hud.fit(lane["model"], ml_w - (ep.get_width() + p6 if ep else 0), px(14), (196, 204, 222), False, min_size=px(11))
                surf.blit(mi, (tx, yy))
                mx, mh = tx + mi.get_width() + p6, mi.get_height()
            if ep:
                surf.blit(ep, (mx, yy + max(0, (mh - ep.get_height()) // 2)))
                mh = max(mh, ep.get_height())
            yy += mh
            # 底部一行：实际距离 · 冲线 · 摔倒/撞栏次数
            fin = f"冲线 {res['finish_time']:.2f}s" if res["finish_time"] is not None else "未冲线"
            if lane["load_error"]:
                fin = "载入失败"
            nf, nh = len(lane["fx"].falls), len(lane["fx"].hurdle_hits)
            extra = (f" · 摔倒 {nf}" if nf else "") + (f" · 撞栏 {nh}" if nh else "")
            detail = hud.fit(f"实际 {res['final']['x']:.2f}m · {fin}{extra}", card.right - p12 - tx, px(13), (176, 184, 202))
            dy = card.bottom - detail.get_height() - p7
            if dy > yy + p4:
                surf.blit(detail, (tx, dy))

    # -- 悬停 / 帮助 --
    def _draw_hover(self, surf, pos, k):
        mx, my = pos
        tl = self.timeline_rect()
        if ROW_A <= my < TOP and tl.x - px(8) <= mx <= tl.right + px(8):
            kk = self.frame_at_x(mx)
            img = text(f"跳到 {kk / FPS:.2f}s", px(13), (255, 255, 255), True)
            r = pygame.Rect(0, 0, img.get_width() + px(12), img.get_height() + px(4))
            r.midtop = (max(px(40), min(W - px(40), mx)), TOP + px(4))
            surf.blit(fx.panel(r.w, r.h, (20, 24, 32), 230, px(6)), r)
            surf.blit(img, (r.x + px(6), r.y + px(2)))
            return
        i = self.lane_at(pos)
        if i is None:
            return
        lane = self.lanes[i]
        f = lane["frames"][k]
        ml = hud.model_line(lane["model"], lane.get("effort", ""))
        lines = [(lane["name"] + (f" ({ml})" if ml else ""), True),
                 (f"时间 {f['time']:.2f}s", False), (f"实际 x {f['x']:.3f} m", False), (f"成绩 {f['score']:.3f} m", False),
                 (f"速度 vx {f['vx']:+.2f} m/s", False), (f"躯干角 {math.degrees(f['torso_angle']):+.1f}°", False),
                 (f"躯干高度 {f['y']:.2f} m", False), (f"姿态 {label_text(lane['labels'][k])}", False)]
        imgs = [text(s, px(14), (255, 255, 255) if b else (225, 230, 240), b) for s, b in lines]
        bw = max(im.get_width() for im in imgs) + px(20)
        bh = sum(im.get_height() for im in imgs) + px(14)
        x = mx + px(18) if mx + px(18) + bw < W else mx - px(18) - bw
        y = max(TOP + px(4), min(H - bh - px(4), my + px(12)))
        surf.blit(fx.panel(bw, bh, (20, 24, 32), 225, px(8)), (x, y))
        pygame.draw.rect(surf, self.colors[i], (x, y, bw, bh), px(2), border_radius=px(8))
        yy = y + px(7)
        for im in imgs:
            surf.blit(im, (x + px(10), yy))
            yy += im.get_height()

    def _draw_help(self, surf):
        rows = [("空格", "暂停 / 继续"), ("← / →", "暂停时逐帧，播放中 ±2 秒"), ("↑ / ↓ / 滚轮", "倍速 0.25x ~ 4x"),
                (", / .", "跳到上一个 / 下一个事件（摔倒·撞栏·冲线）"), ("B", "慢动作 0.25x 开关"), ("F", "追拍：自动放大领先者"),
                ("G", "切换 叠放 / 四宫格"), ("J", "关节波形：4 条 / 全部 6 条"), ("TAB", "显示 / 隐藏 HUD"),
                ("S", "截图保存到当前文件夹"), ("R", "从头重播"), ("H", "显示 / 隐藏本帮助"), ("Q / Esc", "退出"),
                ("鼠标拖动时间轴", "跳到任意时刻"), ("鼠标点击赛道", "放大该赛道（再点还原）"), ("鼠标悬停赛道", "查看精确数值")]
        bw, rh = px(620), px(26)
        bh = px(70) + len(rows) * rh
        x, y = (W - bw) // 2, TOP + (H - TOP - bh) // 2
        surf.blit(fx.panel(bw, bh, (18, 22, 30), 235, px(12)), (x, y))
        pygame.draw.rect(surf, GOLD, (x, y, bw, bh), px(2), border_radius=px(12))
        fx.text_shadow(surf, "操作说明", px(22), GOLD, (x + bw // 2, y + px(14)), bold=True, center=True)
        for j, (key, desc) in enumerate(rows):
            yy = y + px(56) + j * rh
            surf.blit(text(key, px(15), (255, 230, 150), True), (x + px(30), yy))
            surf.blit(text(desc, px(15), (230, 234, 242)), (x + px(200), yy))

    # -- 片头 --
    def draw_intro(self, surf, t):
        if UI_STYLE == "game":
            return gui.draw_intro(self, surf, t)
        bg = fx._cached(("intro_bg",), lambda: _gradient(W, H, (22, 30, 60), (60, 26, 70)))
        surf.blit(bg, (0, 0))
        cx, cy = W // 2, px(250)
        for j in range(14):          # 旋转的放射光
            a = j * math.pi / 7 + t * 0.25
            L = px(1500)
            pygame.draw.polygon(surf, (40, 46, 84) if j % 2 else (32, 38, 72),
                                [(cx, cy), (cx + math.cos(a) * L, cy + math.sin(a) * L),
                                 (cx + math.cos(a + 0.22) * L, cy + math.sin(a + 0.22) * L)])
        e = _ease(t / 0.5)
        fx.text_shadow(surf, "QWOP AI 运动会", px(64), GOLD, (cx, int(pf(90) - (1 - e) * pf(140))), bold=True, center=True, shadow=(60, 30, 0))
        sub = f"{physics.TRACKS[self.track]['label']} · {fx.MODE_LABEL.get(self.mode, self.mode)} · {self.sim_time:g} 秒 · 终点 {physics.TRACK_LENGTH:g} 米"
        e2 = _ease((t - 0.25) / 0.4)
        if e2 > 0:
            img = text(sub, px(24), (230, 234, 245))
            img.set_alpha(int(255 * e2))
            surf.blit(img, (cx - img.get_width() // 2, px(180)))
        n = len(self.lanes)
        g20 = px(20)
        cw = min(px(280), (W - px(80)) // n - g20)
        total = n * cw + (n - 1) * g20
        for i, lane in enumerate(self.lanes):
            ei = _ease((t - 0.5 - 0.15 * i) / 0.45)
            if ei <= 0:
                continue
            r = pygame.Rect((W - total) // 2 + i * (cw + g20), int(pf(372) + (1 - ei) * pf(260)), cw, px(180))   # 原 330，下移给站在卡片上的小人让位
            pygame.draw.rect(surf, shade(self.colors[i], 0.55), r.move(0, px(6)), border_radius=px(16))
            pygame.draw.rect(surf, self.colors[i], r, border_radius=px(16))
            pygame.draw.rect(surf, (255, 255, 255), r, px(3), border_radius=px(16))
            # 选手造型亮相：站在卡片上沿（第 0 帧的站姿，脚底 = 物理地面 y≈0.02m）
            sc = min(pf(80.0), cw / 2.4)
            stand = type("V", (), {})()
            stand.scale = sc
            stand.ax = r.centerx - lane["poses"][0][0][0] / PPM * sc
            stand.ay = r.y + px(1)
            pygame.draw.ellipse(surf, shade(self.colors[i], 0.6), (r.centerx - sc * 0.4, r.y - px(3), sc * 0.8, px(7)))
            head = self._draw_runner(surf, stand, lane["poses"][0], self.run_colors[i], "grin", t * FPS, lane_i=i)
            ch = self.chars[i]
            if ch is not None and ch.head is not None:
                self._draw_avatar(surf, ch, head, lane["poses"][0][0][2], "normal", t * FPS)
            fx.text_shadow(surf, f"第 {i + 1} 道", px(18), (255, 255, 255), (r.centerx, r.y + px(14)), center=True, shadow=shade(self.colors[i], 0.5))
            name = lane["name"]
            fs = px(34)
            while fs > px(16) and text(name, fs, (255, 255, 255), True).get_width() > cw - px(20):
                fs -= px(2)
            fx.text_shadow(surf, name, fs, (255, 255, 255), (r.centerx, r.y + px(50)), bold=True, center=True, shadow=shade(self.colors[i], 0.4))
            if lane["model"]:        # 模型名：缩字号/省略号，不超出卡片
                fg = hud.fit(lane["model"], cw - px(24), px(20), (255, 245, 220), True, min_size=px(13))
                sh = hud.fit(lane["model"], cw - px(24), px(20), shade(self.colors[i], 0.4), True, min_size=px(13))
                mx = r.centerx - fg.get_width() // 2
                surf.blit(sh, (mx + px(1), r.y + px(107)))
                surf.blit(fg, (mx, r.y + px(106)))
            if lane.get("effort"):
                ep = hud.cpill(lane["effort"], px(16), shade(self.colors[i], 0.45), (255, 255, 255))
                surf.blit(ep, (r.centerx - ep.get_width() // 2, r.y + px(140)))
        if n > 1 and t > 1.0:
            img = text("预备 · 跑！", px(30), GOLD, True)
            surf.blit(img, (cx - img.get_width() // 2, px(594)))


def _initials(names):
    """进度条标记上的缩写：默认首字母；首字母撞车的改用各单词首字母或前两个字，仍撞车就用赛道号。"""
    def words(n):
        parts = [p for p in n.replace("-", " ").replace("_", " ").split() if p]
        return "".join(p[0] for p in parts)[:2]
    cand = [n[:1].upper() for n in names]
    for f in (words, lambda n: n[:2]):
        dup = [i for i, c in enumerate(cand) if cand.count(c) > 1]
        for i in dup:
            cand[i] = f(names[i]).upper() or cand[i]
    if len(set(cand)) != len(cand) or not all(cand):
        return [str(i + 1) for i in range(len(names))]
    return cand


def _overlap(a, b):
    c = a.clip(b)
    return c.w * c.h


def _gradient(w, h, top, bot):
    s = pygame.Surface((w, h)).convert()
    for y in range(h):
        pygame.draw.line(s, mix(top, bot, y / max(1, h - 1)), (0, y), (w, y))
    return s


# ------------------------------------------------------------ 交互 --------
class App:
    """交互回放的状态机（不依赖真实窗口，测试时直接喂 pygame 事件）。"""

    def __init__(self, renderer, ui, speed=1.0, auto_slowmo=False):
        self.r, self.ui = renderer, ui
        self.last = renderer.n_frames - 1
        self.pos = 0.0
        self.si = min(range(len(SPEEDS)), key=lambda j: abs(SPEEDS[j] - speed))
        self.auto = auto_slowmo
        self.mult = 1.0             # 当前慢动作倍率（在 1 与 SLOW 之间渐变）
        self.drag = False
        self.want_shot = False
        self.shot_dir = os.getcwd()

    @property
    def k(self):
        return int(self.pos)

    def seek(self, k):
        self.pos = float(max(0, min(self.last, k)))
        if self.pos < self.last:
            self.ui.end_t = None

    def _toast(self, s):
        self.ui.toast = (s, 2.0)

    def handle(self, ev):
        """处理一个事件；返回 False 表示退出。"""
        ui = self.ui
        if ev.type == pygame.QUIT:
            return False
        if ev.type == pygame.MOUSEWHEEL:
            self.si = max(0, min(len(SPEEDS) - 1, self.si + (1 if ev.y > 0 else -1)))
        elif ev.type == pygame.MOUSEMOTION:
            ui.hover = ev.pos
            if self.drag:
                self.seek(self.r.frame_at_x(ev.pos[0]))
        elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            mx, my = ev.pos
            if ROW_A <= my < TOP:
                self.drag = True
                self.seek(self.r.frame_at_x(mx))
            elif my >= TOP:
                i = self.r.lane_at(ev.pos)
                if i is not None and len(self.r.lanes) > 1:
                    ui.focus = None if ui.focus == i else i
        elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
            self.drag = False
        elif ev.type == pygame.KEYDOWN:
            key = ev.key
            if key in (pygame.K_q, pygame.K_ESCAPE):
                return False
            if key == pygame.K_SPACE:
                ui.paused = not ui.paused
            elif key == pygame.K_r:
                self.pos, ui.paused, ui.end_t = 0.0, False, None
            elif key == pygame.K_TAB:
                ui.show_hud = not ui.show_hud
            elif key == pygame.K_UP:
                self.si = min(len(SPEEDS) - 1, self.si + 1)
            elif key == pygame.K_DOWN:
                self.si = max(0, self.si - 1)
            elif key in (pygame.K_RIGHT, pygame.K_LEFT):
                d = 1 if key == pygame.K_RIGHT else -1
                if ui.paused:           # 逐帧：落在整数帧上（停在两帧之间时，← 先回到前一个整数帧）
                    self.seek(self.k + 1 if d > 0 else math.ceil(self.pos) - 1)
                else:
                    self.seek(self.k + d * 2 * FPS)
            elif key in (pygame.K_COMMA, pygame.K_PERIOD):
                self._jump_event(1 if key == pygame.K_PERIOD else -1)
            elif key == pygame.K_b:
                ui.slow = not ui.slow
            elif key == pygame.K_f:
                ui.follow, ui.focus = not ui.follow, None
            elif key == pygame.K_g:
                ui.layout = "grid" if ui.layout == "stack" else "stack"
            elif key == pygame.K_j and UI_STYLE == "game":
                gui.cycle_wave(ui)
            elif key == pygame.K_j:
                ui.all_joints = not ui.all_joints
            elif key == pygame.K_h:
                ui.show_help = not ui.show_help
            elif key == pygame.K_s:
                self.want_shot = True
        return True

    def _jump_event(self, d):
        targets = sorted({max(0, ke - EVENT_LEAD) for ke, _i, _k in self.r.events})
        if not targets:
            self._toast("这场比赛没有摔倒/撞栏/冲线事件")
            return
        k = self.k
        nxt = [t for t in targets if t > k] if d > 0 else [t for t in targets if t < k - 5]
        if not nxt:
            self._toast("已经是最后一个事件" if d > 0 else "已经是第一个事件")
            return
        t = nxt[0] if d > 0 else nxt[-1]
        ke, i, kind = next(e for e in self.r.events if max(0, e[0] - EVENT_LEAD) == t)
        self.seek(t)
        self._toast(f"{self.r.lanes[i]['name']}：{ {'fall': '摔倒', 'hurdle': '撞栏', 'finish': '冲线'}[kind] } @ {ke / FPS:.2f}s")

    def speed(self):
        slow = self.ui.slow or (self.auto and self.r.in_slow_window(self.k))
        return SPEEDS[self.si] * (SLOW if slow else 1.0), slow

    def tick(self, dt):
        """回放时钟：pos 是浮点帧号，pos += 倍速 * dt * 60；画面在相邻两帧之间插值。"""
        ui = self.ui
        _sp, ui.slow_active = self.speed()
        self.mult = ramp_mult(self.mult, SLOW if ui.slow_active else 1.0, dt)
        sp = SPEEDS[self.si] * self.mult
        ui.speed = sp
        if not ui.paused and not self.drag:
            self.pos = min(self.last, self.pos + dt * FPS * sp)
        if self.k >= self.last:
            ui.end_t = 0.0 if ui.end_t is None else ui.end_t + dt
        else:
            ui.end_t = None
        if ui.toast:
            ui.toast = (ui.toast[0], ui.toast[1] - dt) if ui.toast[1] > dt else None

    def draw(self, surf):
        if self.want_shot:
            self.want_shot = False
            toast, self.ui.toast = self.ui.toast, None
            self.r.draw(surf, self.pos, self.ui)
            name = time.strftime("qwop_%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}.png"
            path = os.path.join(self.shot_dir, name)
            pygame.image.save(surf, path)
            self._toast(f"已保存截图 {name}")
            return path
        self.r.draw(surf, self.pos, self.ui)
        return None


def window_size():
    """交互窗口尺寸：画布放得下就 1:1；--res 1080 而屏幕放不下时按比例缩小显示（画面仍按 1080p 绘制，S 截图是全分辨率）。"""
    if RES == 720:
        return W, H
    try:
        info = pygame.display.Info()
        sw, sh = info.current_w, info.current_h
    except pygame.error:
        sw = sh = 0
    if sw <= 0 or sh <= 0:
        return W, H
    f = min(1.0, (sw * 0.95) / W, (sh * 0.88) / H)
    return int(W * f), int(H * f)


def _map_event(ev, f):
    """窗口缩小显示时，把鼠标坐标换算回画布坐标。"""
    if f == 1.0 or not hasattr(ev, "pos"):
        return ev
    d = dict(ev.dict)
    d["pos"] = (int(ev.pos[0] / f), int(ev.pos[1] / f))
    if "rel" in d:
        d["rel"] = (int(ev.rel[0] / f), int(ev.rel[1] / f))
    return pygame.event.Event(ev.type, d)


def interactive(renderer, ui, speed, auto_slowmo):
    ww, wh = window_size()
    screen = pygame.display.set_mode((ww, wh))
    pygame.display.set_caption("QWOP AI 运动会 · 回放")
    clock = pygame.time.Clock()
    app = App(renderer, ui, speed, auto_slowmo)
    ui.interactive = True
    f = ww / W
    canvas = screen if (ww, wh) == (W, H) else pygame.Surface((W, H)).convert()
    while True:
        dt = clock.tick(FPS) / 1000.0
        for ev in pygame.event.get():
            if not app.handle(_map_event(ev, f)):
                return
        app.tick(dt)
        app.draw(canvas)
        if canvas is not screen:
            pygame.transform.smoothscale(canvas, (ww, wh), screen)
        pygame.display.flip()


# ------------------------------------------------------------ 导出 --------
def _intro_frame(renderer, surf, j, ui):
    t = j / FPS
    renderer.draw_intro(surf, t)
    fade = INTRO_SECONDS - 0.35
    if t > fade:                       # 最后 0.35 秒淡入比赛第一帧
        race = pygame.Surface((W, H))
        if UI_STYLE == "game" and COUNTDOWN:        # 游戏版：淡入倒计时的第一帧
            gui.draw_countdown(renderer, race, 0.0, ui)
        else:
            renderer.draw(race, 0, ui)
        race.set_alpha(int(255 * (t - fade) / 0.35))
        surf.blit(race, (0, 0))


EXPORT_QUALITY = "normal"   # normal: crf20/medium；high: crf12/slow/tune animation（画面更干净，文件约 2~3 倍）
EXPORT_COVER = None         # --cover 秒：导出视频的第一帧（封面）用回放里该时刻的画面，之后正常放片头/比赛
EXPORT_COVER_FRAMES = 4
EXPORT_BGM = None           # 背景音乐文件（--bgm）；导出后混入，循环铺满、1 秒淡入、结尾淡出
EXPORT_BGM_VOL = 0.35
EXPORT_UPSCALE = 0          # 0=原尺寸（画布 W x H）；1080/1440=用 lanczos 放大到该高度（平台二次压缩时更不糊；与画布同高时不放大）


TRIM_AFTER_FINISH = None     # --trim-after-finish：所有赛道都冲线后再播这么多秒就进成绩榜（P2 单人回放用，省掉等到 30 秒的空档）
TRIM_STILL_DOWN = None       # --trim-still-down：倒地后躯干连续这么多秒几乎不动就进成绩榜
STILL_POS, STILL_ANG = 0.10, 0.30   # "几乎不动"：这段时间里躯干 x、y 位移都不超过 0.10 m，倾角变化不超过 0.30 rad


def _still_down_frame(lane, secs):
    """第一个满足"从某帧起一直处于倒地、躯干位置/倾角基本没变、持续够 secs 秒"的回放帧；没有返回 None。"""
    fr, down = lane["frames"], lane["down"]
    need = int(secs * FPS)
    a = None
    for k in range(len(fr)):
        if not down[k]:
            a = None
            continue
        if a is None or abs(fr[k]["x"] - fr[a]["x"]) > STILL_POS or abs(fr[k]["y"] - fr[a]["y"]) > STILL_POS                 or abs(fr[k]["torso_angle"] - fr[a]["torso_angle"]) > STILL_ANG:
            a = k
        elif k - a >= need:
            return k
    return None


def _trim_last(renderer):
    """导出时回放的最后一帧：默认 = 整段。设了 TRIM_AFTER_FINISH / TRIM_STILL_DOWN 时，每条赛道各算一个"可以收尾"的帧：
    冲线后再过 TRIM_AFTER_FINISH 秒，或倒地后躯干不动满 TRIM_STILL_DOWN 秒（取先到的）；所有赛道都有这个帧才截，截在最晚的那个。
    成绩榜仍按完整模拟的结果显示。"""
    last = renderer.n_frames - 1
    if TRIM_AFTER_FINISH is None and TRIM_STILL_DOWN is None:
        return last
    fin = {}
    for k, i, kind in renderer.events:
        if kind == "finish":
            fin.setdefault(i, k)
    ends = []
    for i, L in enumerate(renderer.lanes):
        cand = []
        if TRIM_AFTER_FINISH is not None and i in fin:
            cand.append(fin[i] + int(TRIM_AFTER_FINISH * FPS))
        if TRIM_STILL_DOWN is not None:
            ks = _still_down_frame(L, TRIM_STILL_DOWN)
            if ks is not None:
                cand.append(ks)
        if not cand:
            return last
        ends.append(min(cand))
    return min(last, max(ends))


def _playback(renderer, speed, auto_slowmo):
    """导出时每个输出帧（1/60 秒）对应的回放位置：逐帧给出 (pos, 本帧倍速, 是否在慢动作窗口)。
    save_video 和 export_events 共用，保证事件表的时间和导出视频逐帧一致。"""
    last = _trim_last(renderer)
    pos, mult = 0.0, 1.0
    while True:
        pos = min(float(last), pos)
        slow = auto_slowmo and renderer.in_slow_window(int(pos))
        man = renderer.manual_slow(int(pos))
        target = min(SLOW if slow else 1.0, man if man is not None else 1.0)
        mult = ramp_mult(mult, target, 1.0 / FPS)
        yield pos, speed * mult, slow or man is not None
        if pos >= last:
            return
        pos += speed * mult


def _lead_frames(intro):
    """比赛第 0 帧之前的输出帧数：封面帧 + 片头 + 倒计时（与 save_video 的顺序一致）。"""
    n = EXPORT_COVER_FRAMES if EXPORT_COVER is not None else 0
    if intro:
        n += int(INTRO_SECONDS * FPS)
    if UI_STYLE == "game" and COUNTDOWN:
        n += int(round(gui.COUNT_TOTAL * FPS))
    return n


EVENT_LABELS = {"fall": "摔倒", "hurdle": "撞栏", "finish": "冲线"}


def export_events(renderer, path, speed, auto_slowmo=False, intro=False):
    """C17 事件时间表：不画画面，按导出时的同一套帧序把每个事件换算成视频时间，写 <path>.json + <path>.csv。
    事件 = 摔倒 / 撞栏 / 冲线（与时间轴标记、自动慢动作同一份 renderer.events）；另记比赛开始、比赛结束、慢动作区间。"""
    import csv
    lead = _lead_frames(intro)
    first_out = {}                     # 回放帧 k → 第一个画到它（pos ≥ k）的输出帧
    slow_ranges, cur, n_out = [], None, 0
    pos_s = []                         # 每个比赛输出帧对应的比赛时间（秒），剪辑换算 视频↔比赛 时间用
    for j, (pos, _sp, slow) in enumerate(_playback(renderer, speed, auto_slowmo)):
        pos_s.append(round(pos / FPS, 4))
        for k in range(len(first_out), int(pos) + 1):
            first_out[k] = j
        if slow and cur is None:
            cur = j
        elif not slow and cur is not None:
            slow_ranges.append((cur, j))
            cur = None
        n_out = j + 1
    if cur is not None:
        slow_ranges.append((cur, n_out))
    race_end = lead + n_out
    total = race_end + int(END_HOLD * FPS)

    def vt(f):
        return round(f / FPS, 3)

    rows, count = [], {}
    for k, i, kind in renderer.events:
        L = renderer.lanes[i]
        count[(i, kind)] = count.get((i, kind), 0) + 1
        f = lead + first_out.get(k, n_out - 1)
        note = f"{L['result']['finish_time']:.2f}s" if kind == "finish" else f"第 {count[(i, kind)]} 次"
        rows.append(dict(video_s=vt(f), video_frame=f, lane=i + 1, name=L["name"], event=kind, label=EVENT_LABELS[kind],
                         sim_s=round(k / FPS, 3), note=note))
    data = dict(fps=FPS, video_frames=total, video_s=vt(total), race_start_s=vt(lead), race_end_s=vt(race_end),
                board_s=vt(race_end), lanes=[L["name"] for L in renderer.lanes],
                slow_ranges_s=[[vt(lead + a), vt(lead + b)] for a, b in slow_ranges], events=rows,
                manual_slow=[[round(a / FPS, 3), round(b / FPS, 3), f] for a, b, f in SLOW_RANGES], pos_s=pos_s)
    base = os.path.splitext(path)[0] if path.endswith((".json", ".csv")) else path
    os.makedirs(os.path.dirname(os.path.abspath(base)), exist_ok=True)
    with open(base + ".json", "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=1)
    with open(base + ".csv", "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=list(rows[0]) if rows else ["video_s"])
        w.writeheader()
        w.writerows(rows)
    print(f"已导出事件表 {base}.json / .csv（{len(rows)} 个事件，视频 {vt(total)}s）")


def save_video(renderer, out, ui, speed, auto_slowmo=False, intro=False):
    surf = pygame.Surface((W, H))
    enc = ["-crf", "20", "-preset", "medium"] if EXPORT_QUALITY == "normal" else ["-crf", "12", "-preset", "slow", "-tune", "animation"]
    vf = ["-vf", f"scale={EXPORT_UPSCALE * W // H}:{EXPORT_UPSCALE}:flags=lanczos"] if EXPORT_UPSCALE and EXPORT_UPSCALE != H else []
    final_out = out
    if EXPORT_BGM:
        out = out + ".silent.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", *vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", *enc, out]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    def emit():
        proc.stdin.write(pygame.image.tobytes(surf, "RGB"))

    if EXPORT_COVER is not None:
        kc = _frame_at(str(EXPORT_COVER), renderer.n_frames)
        ui.end_t = None
        renderer.draw(surf, float(kc), ui)
        for _ in range(EXPORT_COVER_FRAMES):
            emit()
    if intro:
        for j in range(int(INTRO_SECONDS * FPS)):
            _intro_frame(renderer, surf, j, ui)
            emit()
    if UI_STYLE == "game" and COUNTDOWN:          # 3-2-1-GO 放在回放第 0 帧之前（盖在第 0 帧画面上），不改变比赛帧
        for j in range(int(round(gui.COUNT_TOTAL * FPS))):
            gui.draw_countdown(renderer, surf, j / FPS, ui)
            emit()
    last = _trim_last(renderer)
    # 每个输出帧（1/60 秒）采样一次回放位置；慢动作/非整数倍速时画相邻两帧之间的插值画面，
    # 进出慢动作窗口时倍率在 SLOW_RAMP 秒内渐变
    for pos, sp, slow in _playback(renderer, speed, auto_slowmo):
        ui.speed, ui.slow_active = sp, slow
        renderer.draw(surf, pos, ui)
        emit()
    for j in range(int(END_HOLD * FPS)):      # 结束画面（成绩榜滑入 + 计数动画）停留几秒
        ui.end_t = j / FPS
        renderer.draw(surf, last, ui)
        emit()
    proc.stdin.close()
    if proc.wait() != 0:
        sys.exit("ffmpeg 编码失败")
    if EXPORT_BGM:
        dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out], text=True).strip())
        af = f"volume={EXPORT_BGM_VOL},afade=t=in:d=1,afade=t=out:st={max(dur - 2.5, 0):.2f}:d=2.5"
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", out, "-stream_loop", "-1", "-i", EXPORT_BGM, "-map", "0:v", "-map", "1:a",
                            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-af", af, "-t", f"{dur:.3f}", final_out])
        os.remove(out)
        if r.returncode != 0:
            sys.exit("ffmpeg 混入背景音乐失败")
        out = final_out
    print(f"已导出 {out}")


# ------------------------------------------------------------ 主程序 ------
def prepare(bots, names, mode, sim_time, track, starts=None, bubbles="few"):
    """跑完所有选手并生成特效数据。names 里可写 "名字|模型 强度" / "名字|模型|强度"（见 viz_hud.parse_entry）。"""
    names = list(names or [])
    lanes = []
    for i, path in enumerate(bots):
        raw = names[i] if i < len(names) else os.path.splitext(os.path.basename(path))[0]
        name, model, effort = hud.parse_entry(raw)
        if UI_STYLE == "game" and model and not effort:       # 游戏风格：写了模型却没写思考等级的，一律显示 high
            effort = DEFAULT_EFFORT
        lanes.append(run_bot(path, name, mode, sim_time, track, model,
                              start=(starts[i % len(starts)] if starts else None), effort=effort))
    attach_fx(lanes, track, bubbles)
    return lanes


def setup_characters(lanes, avatars=None, colors=None, names_given=0, root=None, separate=True):
    """角色化（只在给了 --avatars / --colors 时调用）：给每条赛道挂上 char（fx.Character）、主题色。
    avatars：按赛道顺序的角色 key（不分大小写，可用别名 Qwen/doubao…；'-' = 这条赛道不用；
             只写一个 'auto' = 全部从 --names / bot 文件名推断；某条写 'auto' = 只推断这条）。
    colors： 按赛道顺序覆盖界面主色（'#ff8800' / '255,136,0' / '-' 不覆盖）。
    names_given：--names 给了几条；没给名字的赛道用角色 key 当显示名。
    返回提示信息列表（打印用）。"""
    root = root or fx.AVATAR_DIR
    msgs = []
    n = len(lanes)
    toks = list(avatars or [])
    if len(toks) == 1 and toks[0].lower() == "auto":
        toks = ["auto"] * n
    keys = [None] * n
    for i in range(n):
        t = toks[i] if i < len(toks) else None
        if t is None:
            continue
        if t.lower() == "auto":
            L = lanes[i]
            keys[i] = fx.guess_avatar(L["name"], L["model"], os.path.splitext(os.path.basename(L["path"]))[0], root=root)
            msgs.append(f"第 {i + 1} 道 auto → {keys[i] or '（没认出来，不贴头像）'}")
            continue
        k = fx.resolve_avatar(t, root)
        if k is False:
            msgs.append(f"第 {i + 1} 道：找不到角色 {t!r}（可用：{' '.join(fx.avatar_keys(root))}）")
            k = None
        keys[i] = k
    ui_cols, run_cols, themes, locked = [], [], [], []
    for i, key in enumerate(keys):
        th = fx.theme_colors(key) if key else None
        if th:
            main, accent, uic = th
        else:
            main = accent = uic = LANE_COLORS[i % len(LANE_COLORS)]
        over = fx.parse_color(colors[i]) if colors and i < len(colors) else None
        if over is not None:
            if main == uic:
                main = over
            uic = over
        ui_cols.append(uic)
        run_cols.append(main)
        themes.append((main, accent) if key else None)
        locked.append(over is not None)
    if separate:                           # 同屏颜色拉开（--colors 显式指定的不动）
        new = list(ui_cols)
        for i in range(n):
            if locked[i]:
                continue
            prev = [new[j] for j in range(n) if j < i or locked[j]]
            old = new[i]
            new[i] = fx.distinct_from(old, prev)
            if new[i] != old:
                if run_cols[i] == old:
                    run_cols[i] = new[i]
                    if themes[i]:
                        themes[i] = (new[i], themes[i][1])
                msgs.append(f"第 {i + 1} 道主题色与同屏赛道太接近：{old} → {new[i]}（可用 --colors 指定）")
        ui_cols = new
    for i, L in enumerate(lanes):
        key = keys[i]
        L["ui_color"], L["run_color"], L["theme"] = ui_cols[i], run_cols[i], themes[i]
        L["char"] = None
        if key:
            ch = fx.Character(key, root)
            if ch.head is None and not ch.q:
                msgs.append(f"第 {i + 1} 道：{os.path.join(root, key)} 下没有 head.png / q_*.png，只换主题色")
            L["char"] = ch
            L["avatar_key"] = key
            if i >= names_given:
                L["name"] = key
    return msgs


def _frame_at(t_str, n_frames):
    if t_str == "end":
        return n_frames - 1
    return max(0, min(n_frames - 1, int(round(float(t_str) * FPS))))


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="QWOP Arena 同屏回放")
    ap.add_argument("bots", nargs="+", help="bot 文件路径（1~4 个）")
    ap.add_argument("--track", choices=sorted(physics.TRACKS), default=physics.DEFAULT_TRACK, help="obstacle=障碍赛, flat=无障碍")
    ap.add_argument("--mode", choices=sorted(physics.MODES), default="normal")
    ap.add_argument("--time", type=float, default=physics.SIM_TIME, help="模拟时长 (秒)")
    ap.add_argument("--speed", type=float, default=1.0, help="回放倍速")
    ap.add_argument("--names", nargs="+", help='各赛道显示的选手名："名字" / "名字|模型" / "名字|模型 high" / "名字|模型|high"'
                    '（模型和推理强度显示在选手卡、片头和成绩榜）')
    ap.add_argument("--bubbles", choices=fx.BUBBLE_MODES, default="few",
                    help="头顶气泡：few=只在摔倒/爬起/新赛段等时刻短暂冒出（默认），normal=旧版一直显示当前姿态，off=不显示")
    ap.add_argument("--layout", choices=["stack", "grid"], default="stack", help="stack=上下叠放, grid=3~4 人时 2x2 四宫格")
    ap.add_argument("--start", help="倒地起点 prone/supine，逗号分隔按选手顺序（起身回放用，配合 --time 20）")
    ap.add_argument("--quality", choices=["normal", "high"], default="normal", help="导出画质：high=crf12+slow+tune animation")
    ap.add_argument("--upscale", type=int, default=0, choices=[0, 1080, 1440], help="导出时用 lanczos 放大到该高度（1080/1440），平台再压缩时更清晰")
    ap.add_argument("--res", type=int, default=720, choices=[720, 1080],
                    help="画布分辨率：720=1280x720（默认）；1080=1920x1080 原生绘制（字号/线宽/布局/头像都按 1.5 倍重画，不是放大）")
    ap.add_argument("--body-style", choices=["v1", "v2", "robot"], default="v1",
                    help="小人画法：v1=原画法（默认）；v2=运动服 Q 版小人（圆润四肢、背心号码布、短裤/袜子/跑鞋，物理形状不变）；robot=机器人（CRT 电视头 + 工业风骨架，--avatars 时头像嵌进电视屏幕）")
    ap.add_argument("--bgm", nargs="?", const="assets/bgm.m4a", default=None, help="导出时混入背景音乐（不带路径=自带合成曲 assets/bgm.m4a，可换成自己的 mp3/m4a）")
    ap.add_argument("--bgm-volume", type=float, default=0.35, help="背景音乐音量 0~1（默认 0.35）")
    ap.add_argument("--cover", default=None, help="导出视频的第一帧用回放里该秒数的画面（挑一个有趣的瞬间当封面），之后正常放片头")
    ap.add_argument("--cover-frames", type=int, default=4, help="封面帧持续的帧数（60fps，默认 4 帧）")
    ap.add_argument("--follow", action="store_true", help="开局即打开追拍（F 键）")
    ap.add_argument("--auto-slowmo", action="store_true", help="事件前后自动慢动作（默认：每次摔倒前 0.5s 后 1s，0.25x；可用下面三个参数调整）")
    ap.add_argument("--slow-events", default=None, metavar="A,B",
                    help=f"自动慢动作由哪些事件触发，逗号分隔：{'/'.join(SLOW_EVENT_KINDS)}（first-* 每条赛道只取第一次）。默认 fall；"
                         "分组放映推荐 finish,first-hurdle")
    ap.add_argument("--slow-factor", type=float, default=None, help="慢动作倍率 0.1~1（默认 0.25；推荐 0.5）")
    ap.add_argument("--slow-window", default=None, metavar="前,后",
                    help="事件前后各慢放多少秒，如 0.4,0.4（默认 0.5,1.0）")
    ap.add_argument("--slow-ranges", default=None, metavar="A-B:F,...",
                    help="按比赛时间（秒）手动加慢放段，如 4.2-6.0:0.5,11.0-12.5:0.25；与 --auto-slowmo 叠加时取更慢的；原生插值")
    ap.add_argument("--no-fx", action="store_true", help="关闭装饰特效（扬尘/震屏/碎片/速度线/彩纸/气泡动画），调试用")
    ap.add_argument("--save", help="导出 mp4（离屏渲染，不开窗口）")
    ap.add_argument("--intro", action="store_true", help="导出时加 2 秒片头（选手名 + 模型）")
    ap.add_argument("--screenshot", help="离屏渲染单帧 PNG（调试用）")
    ap.add_argument("--at", default="end", help="--screenshot 的时刻（秒）或 end")
    ap.add_argument("--avatars", nargs="+", metavar="KEY",
                    help="角色化：按赛道顺序写角色 key（如 Opus Astra Sonnet 千问；不分大小写，Qwen/doubao 等别名可用；"
                         "'-' = 该赛道不用；只写 auto = 从 --names / bot 文件名推断）。头像贴头、主题色、头像信息卡、Q 版表情弹窗")
    ap.add_argument("--colors", nargs="+", metavar="COLOR",
                    help="按赛道顺序覆盖主题色：#ff8800 / 255,136,0 / -（不覆盖）。不加 --avatars 时只换颜色")
    ap.add_argument("--avatar-dir", default=None, help=f"角色素材目录（默认 {fx.AVATAR_DIR}）")
    ap.add_argument("--no-separate", action="store_true", help="角色化时不自动拉开同屏相近的主题色")
    ap.add_argument("--ui", choices=["classic", "game"], default="classic",
                    help="界面风格：classic=原界面（默认）；game=游戏风格 HUD（选手铭牌、名次盾牌、赛道缩略图、伤害数字、冲线横幅、"
                         "起跑倒计时、结算界面、VS 片头）")
    ap.add_argument("--no-countdown", action="store_true", help="--ui game 导出时不加起跑前的 3-2-1-GO 倒计时")
    ap.add_argument("--waves", action="store_true", help="--ui game 时默认显示关节波形小面板（否则按 J 打开）")
    ap.add_argument("--sub-band", type=int, default=0, metavar="PX",
                    help="底部留一条字幕带（720p 单位，80 = 1080p 下 120px）：赛道区上收，剪辑的口播字幕放带里不挡选手")
    ap.add_argument("--end-hold", type=float, default=None, metavar="秒", help="导出时结束画面（成绩榜）停留秒数（默认 3）")
    ap.add_argument("--trim-after-finish", type=float, default=None, metavar="秒",
                    help="导出时所有赛道都冲线后再播这么多秒就进成绩榜（不用等到 30 秒；成绩榜按完整模拟显示；没人/没全部冲线则不截）")
    ap.add_argument("--trim-still-down", type=float, default=None, metavar="秒",
                    help="导出时倒地后躯干连续不动满这么多秒就进成绩榜（和 --trim-after-finish 可同时用，取先到的；躯干 0.10 m / 0.30 rad 内算不动）")
    ap.add_argument("--events-out", default=None, metavar="PATH",
                    help="C17：按 --save 同样的参数把摔倒/撞栏/冲线换算成视频时间，写 PATH.json + PATH.csv（不渲染画面，可单独用）")
    args = ap.parse_args()
    global EXPORT_QUALITY, EXPORT_UPSCALE, EXPORT_BGM, EXPORT_BGM_VOL, EXPORT_COVER, EXPORT_COVER_FRAMES
    global END_HOLD, TRIM_AFTER_FINISH, TRIM_STILL_DOWN
    TRIM_AFTER_FINISH, TRIM_STILL_DOWN = args.trim_after_finish, args.trim_still_down
    if args.end_hold is not None:
        END_HOLD = max(0.0, args.end_hold)
    EXPORT_COVER, EXPORT_COVER_FRAMES = args.cover, args.cover_frames
    EXPORT_QUALITY, EXPORT_UPSCALE = args.quality, args.upscale
    EXPORT_BGM, EXPORT_BGM_VOL = args.bgm, args.bgm_volume
    if len(args.bots) > 4:
        ap.error("最多 4 个 bot")
    global SUB_BAND
    SUB_BAND = args.sub_band
    set_res(args.res)
    set_body_style(args.body_style)
    if args.ui != "classic":
        set_ui(args.ui, not args.no_countdown)
    global SLOW, SLOW_EVENTS, SLOW_BEFORE, SLOW_AFTER, SLOW_RANGES
    if args.slow_factor is not None:
        if not 0.1 <= args.slow_factor <= 1.0:
            ap.error("--slow-factor 要在 0.1~1 之间")
        SLOW = args.slow_factor
    if args.slow_events is not None:
        ev = tuple(s.strip() for s in args.slow_events.split(",") if s.strip())
        bad = [s for s in ev if s not in SLOW_EVENT_KINDS]
        if bad:
            ap.error(f"--slow-events 不认识 {bad}，可用：{', '.join(SLOW_EVENT_KINDS)}")
        SLOW_EVENTS = ev
    if args.slow_window is not None:
        try:
            b, a = (float(s) for s in args.slow_window.split(","))
        except ValueError:
            ap.error("--slow-window 写成 前,后（秒），如 0.4,0.4")
        SLOW_BEFORE, SLOW_AFTER = int(round(b * FPS)), int(round(a * FPS))
    if args.slow_ranges:
        try:
            for part in args.slow_ranges.split(","):
                ab, f = part.split(":")
                a_, b_ = (float(x) for x in ab.split("-"))
                SLOW_RANGES.append((int(round(a_ * FPS)), int(round(b_ * FPS)), float(f)))
        except ValueError:
            ap.error("--slow-ranges 写成 起-止:倍率，逗号分隔，如 4.2-6.0:0.5")

    offscreen = bool(args.save or args.screenshot or args.events_out)
    if offscreen:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
    pygame.init()
    if offscreen:
        pygame.display.set_mode((1, 1))          # dummy 显示，供 convert() 使用

    lanes = prepare(args.bots, args.names, args.mode, args.time, args.track,
                    args.start.split(",") if args.start else None, args.bubbles)
    for L in lanes:
        r = L["result"]
        fin = f"，{r['finish_time']:.2f}s 冲线" if r["finish_time"] is not None else ""
        err = "（载入失败）" if L["load_error"] else ("（act 报错）" if r["errors"] else "")
        print(f"{L['name']}: 成绩 {r['score']:.2f}m（实际 {r['final']['x']:.2f}m）{fin}{err}")

    if not offscreen:
        pygame.display.set_mode(window_size())
    if args.avatars or args.colors:          # 角色化（不加这两个参数时完全不走这里，画面与改版前逐字节一致）
        for m in setup_characters(lanes, args.avatars, args.colors, len(args.names or []),
                                  args.avatar_dir, separate=not args.no_separate):
            print(m)
    renderer = Renderer(lanes, args.mode, args.track, args.time)
    ui = UI(args.layout, not args.no_fx, args.follow)
    ui.speed = args.speed
    if UI_STYLE == "game":
        ui.show_wave = args.waves
    if args.screenshot and args.at.split(":")[0] in ("intro", "countdown"):     # --at intro:1.2 / countdown:0.5（片头 / 倒计时里的某一刻）
        surf = pygame.Surface((W, H))
        what, sec = args.at.split(":")
        if what == "intro":
            _intro_frame(renderer, surf, int(round(float(sec) * FPS)), ui)
        elif UI_STYLE == "game":
            gui.draw_countdown(renderer, surf, float(sec), ui)
        else:
            sys.exit("--at countdown:秒 只在 --ui game 下有效")
        pygame.image.save(surf, args.screenshot)
        print(f"已保存截图 {args.screenshot}（{args.at}）")
    elif args.screenshot:
        surf = pygame.Surface((W, H))
        k = _frame_at(args.at, renderer.n_frames)
        ui.end_t = 99.0 if k == renderer.n_frames - 1 else None
        renderer.draw(surf, k, ui)
        pygame.image.save(surf, args.screenshot)
        ui.end_t = None
        print(f"已保存截图 {args.screenshot}（第 {k} 帧）")
    if args.events_out:
        export_events(renderer, args.events_out, args.speed, args.auto_slowmo, args.intro)
    if args.save:
        save_video(renderer, args.save, ui, args.speed, args.auto_slowmo, args.intro)
    if not offscreen:
        interactive(renderer, ui, args.speed, args.auto_slowmo)
    pygame.quit()


if __name__ == "__main__":
    main()
