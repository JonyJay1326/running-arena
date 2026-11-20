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
"""
import argparse
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
from viz_fx import DOWN_RED, GOLD, INK, mix, shade, text  # noqa: E402

PPM = physics.PPM
FPS = physics.FPS
W, H = 1280, 720
ROW_A = 60                  # 顶栏上半：标题 + 0~16m 进度
TOP = 84                    # 顶栏总高（下半是可拖动的时间轴）
PANEL_W = 360               # 结束成绩榜宽度（出现时赛道区让出这块）
WAVE_SECONDS = 3.0
SPEEDS = [0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0]
END_HOLD = 3.0              # 导出视频时结束画面停留秒数
INTRO_SECONDS = 2.0
EVENT_LEAD = 30             # ,/. 跳到事件前 0.5 秒
SLOW = 0.25

LANE_COLORS = [(225, 70, 60), (50, 120, 230), (40, 170, 90), (230, 150, 30), (150, 90, 210)]    # 第 5 色：跳远单人 5 跳
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


def run_jump(path, name, mode, model="", effort="", attempts=None):
    """跳远：同一选手的每次试跳各成一条赛道（每跳前重新载入文件），成绩与 evaluator.jump_eval 完全一致。
    跳远没有"倒地不计分"，所以 down 全为 False（不画扣分带/扣分飘字）；姿态标签仍用 evaluator.classify（内部是 is_down）。"""
    load_error = None
    try:
        evaluator.load_bot(path)
        loader = lambda: evaluator.load_bot(path)   # noqa: E731
    except (evaluator.BotError, SyntaxError, OSError) as e:
        load_error = str(e)
        loader = lambda: _always_fail               # noqa: E731
    lanes = []
    for a in range(1, (attempts or physics.TRACKS["jump"]["attempts"]) + 1):
        snap = evaluator._snapshot_shared()
        act = loader()
        poses = [_pose(physics.Runner(mode, "jump", start_x=getattr(act, 'start_x', 0.0)))]
        res = evaluator.jump_attempt(act, a, mode, on_step=lambda r, _o: poses.append(_pose(r)))
        res["leak"] = evaluator._restore_shared(snap)
        frames = res["frames"] + [res["final"]]
        jk = res["judge_k"] if res["judge_k"] is not None else len(frames) - 1
        frames = [dict(f, score=(res["score"] if k >= jk else 0.0)) for k, f in enumerate(frames)]
        cam, c = [], frames[0]["x"]
        for f in frames:
            c += (f["x"] - c) * 0.08
            cam.append(c)
        res["finish_time"] = None
        grade = next(g for th, g, _t in evaluator.JUMP_GRADES if res["score"] >= th)
        lanes.append({"name": f"{name} · 第{a}跳", "model": model, "effort": effort, "path": path, "frames": frames, "poses": poses,
                      "labels": evaluator.classify(frames), "down": [False] * len(frames), "cam": cam, "result": res,
                      "grade": grade, "load_error": load_error, "finish_k": None, "jump": res, "judge_k": jk})
    return lanes


def pad_lanes(lanes):
    """把各赛道补齐到同样帧数（结束后保持最后一帧静止）。"""
    n = max(len(L["frames"]) for L in lanes)
    for L in lanes:
        m = n - len(L["frames"])
        if m > 0:
            for key in ("frames", "poses", "labels", "down", "cam"):
                L[key] = L[key] + [L[key][-1]] * m


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
    return max(20, min(170, int(rect.h / 2.8)))     # px / m：地面以上留约 2.2m


def _anchor(rect):
    return 0.42 if rect.w >= 1100 else 0.5 if rect.w >= 800 else 0.56 if rect.w >= 500 else 0.62


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
        self.slow_windows = [(k - 30, k + 60) for k, _i, kind in self.events if kind == "fall"]
        rr = random.Random(fx.FX_SEED + 999)
        self.rtab = [(rr.random(), rr.random(), rr.random()) for _ in range(257)]
        self.last_layout = []
        self.style = fx.STYLE[mode]
        self.initials = _initials([L["name"] for L in lanes])

    def in_slow_window(self, k):
        return any(a <= k <= b for a, b in self.slow_windows)

    # -- 布局 --
    def layout(self, k, ui):
        n = len(self.lanes)
        area = pygame.Rect(0, TOP, W - (PANEL_W if ui.end_t is not None else 0), H - TOP)
        if n == 1:
            return [(0, area)]
        feat = ui.focus if ui.focus is not None else (self.leader[k] if ui.follow else None)
        if feat is not None:          # 主赛道放大，其余缩成底部的小窗
            sh = 150
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
        return pygame.Rect(16, ROW_A + 9, W - 32, 8)

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
            pygame.draw.rect(surf, BG, rect, 2)
        if len(lay) > 1 and (ui.focus is not None or ui.follow):
            i, rect = lay[0]
            pygame.draw.rect(surf, LANE_COLORS[i], rect, 3)
            tag = "焦点赛道 · 再次点击还原" if ui.focus is not None else "追拍 · 当前领先"
            img = text(tag, 13, (255, 255, 255), True)
            r = pygame.Rect(0, 0, img.get_width() + 16, img.get_height() + 4)
            r.bottomright = (rect.right - 8, rect.bottom - 6)
            pygame.draw.rect(surf, LANE_COLORS[i], r, border_radius=r.h // 2)
            surf.blit(img, (r.x + 8, r.y + 2))
        self._draw_top(surf, k, fr, ui)
        if ui.end_t is not None:
            self._draw_board(surf, ui.end_t)
        if ui.hover is not None:
            self._draw_hover(surf, ui.hover, k)
        if ui.show_help:
            self._draw_help(surf)
        if ui.toast:
            img = text(ui.toast[0], 16, (255, 255, 255), True)
            r = pygame.Rect(0, 0, img.get_width() + 28, img.get_height() + 12)
            r.midbottom = (W // 2, H - 18)
            surf.blit(fx.panel(r.w, r.h, (20, 24, 32), 220, r.h // 2), r)
            surf.blit(img, (r.x + 14, r.y + 6))

    def _draw_placeholder(self, surf, rect):
        pygame.draw.rect(surf, (34, 40, 54), rect)
        fx.text_shadow(surf, "QWOP AI 运动会", 30, GOLD, (rect.centerx, rect.centery - 30), bold=True, center=True)
        sub = f"{physics.TRACKS[self.track]['label']} · {fx.MODE_LABEL.get(self.mode, self.mode)}"
        fx.text_shadow(surf, sub, 18, (210, 216, 228), (rect.centerx, rect.centery + 14), center=True)

    # -- 单条赛道 --
    def _draw_lane(self, surf, i, rect, k, fr, ui):
        """k/fr：整数帧 + 帧内小数。位姿、镜头、阴影、特效时间用 kf = k + fr 连续取值；
        文字类（HUD 数值、气泡姿态、表情）用整数帧 k。"""
        lane = self.lanes[i]
        lfx = lane["fx"]
        frames = lane["frames"]
        kf = k + fr
        color = LANE_COLORS[i]
        scale = _scale_for(rect)
        cx = seq_val(lane["cam"], k, fr)
        cy = 0.5 * physics.terrain_height(cx, self.track)     # 只跟一半坡高，坡顶时远处平地不掉出画面
        ox, oy = lfx.shake(kf, scale) if ui.fx_on else (0.0, 0.0)
        v = View(rect, scale, cx, cy, ox, oy)
        surf.set_clip(rect)
        surf.blit(fx.sky(rect.w, rect.h, self.mode, color), rect.topleft)
        self._draw_parallax(surf, rect, cx * scale, ox, oy)
        self._draw_ground(surf, v)
        self._draw_lost(surf, v, lane, k, fr)
        if "jump" in lane:
            self._draw_jump_marker(surf, v, lane, k)
        pose = lane["poses"][k] if fr == 0 else lerp_pose(lane["poses"][k], lane["poses"][k + 1], fr)
        for idx, verts in self.hurdle_parts:
            fx.draw_hurdle(surf, _poly_screen(v, pose[idx], verts), INK)
        self._draw_shadow(surf, v, frame_val(frames, k, fr, "x"), frame_val(frames, k, fr, "y"))
        if ui.fx_on:
            self._draw_speedlines(surf, v, pose, frame_val(frames, k, fr, "vx"), k, color, i)
        # 视线方向：躯干速度（vy 由相邻两帧躯干高度差分得到），只影响眼珠朝向
        k2 = min(k + 1, self.n_frames - 1)
        vy = (lane["poses"][k2][0][1] - lane["poses"][k2 - 1][0][1]) * FPS / PPM if k2 > 0 else 0.0
        head = self._draw_runner(surf, v, pose, color, self._expression(lane, k), kf,
                                 lane_i=i, look=(frame_val(frames, k, fr, "vx"), vy))
        if ui.fx_on:
            lfx.draw_dust(surf, v, kf, self.style["dust"])
            lfx.draw_debris(surf, v, kf, lambda x: physics.terrain_height(x, self.track))
            lfx.draw_confetti(surf, v, kf)
        self._draw_overhead(surf, v, lane, k, fr, head, color, ui)
        if ui.fx_on:
            self._draw_finish_flash(surf, rect, lane, k, fr)
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
                pygame.draw.line(surf, (160, 30, 30), v.w2s(x, y - th), v.w2s(x + 0.05, y - th - 0.12), 2)
                x += 0.08
            pygame.draw.lines(surf, (200, 30, 30), False, [v.w2s(x, y) for x, y in seg], 3)

    def _draw_jump_marker(self, surf, v, lane, k):
        """跳远：判定后在沙坑里插成绩标记（犯规时在起跳线上插红旗）；最好一跳用金色，并画量尺。"""
        res = lane["jump"]
        if k < lane["judge_k"]:
            return
        line = physics.TRACKS["jump"]["line"]
        fs = max(13, min(22, int(v.scale * 0.15)))
        if res["foul"]:
            x, col, lab = line, DOWN_RED, "犯规"
        elif not res["landed"]:
            return
        else:
            best = max(L["result"]["score"] for L in self.lanes)
            x = max(line, res["ref_x"])
            col = GOLD if res["score"] >= best - 1e-9 and res["score"] > 0 else (240, 240, 245)
            lab = f"{res['score']:.2f}m"
            a, b = v.w2s(line, -0.03), v.w2s(max(line, x), -0.03)
            pygame.draw.line(surf, (255, 255, 255), a, b, max(2, int(v.scale * 0.02)))
            for m in range(1, int(max(0.0, x - line) * 10) + 1):
                p = v.w2s(line + m / 10, -0.03)
                pygame.draw.line(surf, (60, 60, 60), (p[0], p[1] - 3), (p[0], p[1] + 3), 2 if m % 10 == 0 else 1)
        base, tip = v.w2s(x, -0.02), v.w2s(x, 0.75)
        pygame.draw.line(surf, (70, 70, 80), base, tip, max(2, int(v.scale * 0.02)))
        img = text(lab, fs, INK if col != DOWN_RED else (255, 255, 255), True)
        r = pygame.Rect(tip[0], tip[1], img.get_width() + 14, img.get_height() + 6)
        pygame.draw.rect(surf, col, r, border_radius=4)
        pygame.draw.rect(surf, INK, r, 1, border_radius=4)
        surf.blit(img, (r.x + 7, r.y + 3))

    def _draw_shadow(self, surf, v, x, y):
        hgt = max(0.0, y)
        w = int(v.scale * 0.8 * max(0.35, 1 - 0.3 * max(0.0, hgt - 0.6))) // 4 * 4
        if w < 8:
            return
        a = int(80 * max(0.3, 1 - 0.35 * max(0.0, hgt - 1.0))) // 10 * 10
        sx, sy = v.w2s(x, physics.terrain_height(x, self.track))
        img = fx.shadow_sprite(w, max(3, w // 6), a)
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
            pygame.draw.line(surf, col, v.w2s(x0, y), v.w2s(x0 - ln, y), 2)

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
        旧版（纯色矩形 + 白色关节点）备份在 engine/_style_backup/visualizer.py.bak。"""
        return viz_runner.draw_runner(surf, v, pose, color, expr, k, lane_i=lane_i,
                                      label=self.initials[lane_i] if lane_i < len(self.initials) else "", look=look)

    def _loss_pill(self, amount, fs):
        img = text(f"{-amount:+.2f} m 倒地不计分", fs, (255, 255, 255), True)
        s = pygame.Surface((img.get_width() + 14, img.get_height() + 4), pygame.SRCALPHA)
        pygame.draw.rect(s, DOWN_RED, s.get_rect(), border_radius=s.get_height() // 2)
        pygame.draw.rect(s, (255, 255, 255), s.get_rect(), 1, border_radius=s.get_height() // 2)
        s.blit(img, (7, 2))
        return s

    def _draw_overhead(self, surf, v, lane, k, fr, head, color, ui):
        """头顶：实时扣分标签、扣分飘字、姿态气泡（会被夹在赛道矩形内）。
        文字取整数帧 k；位置跟头走（已插值），飘字/弹出动画按 kf 连续计时。"""
        rect, frames, lfx = v.rect, lane["frames"], lane["fx"]
        kf = k + fr
        c, R = head
        fs = max(11, min(17, int(v.scale * 0.11)))
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
            bfs = max(12, min(19, int(v.scale * 0.13)))
            t, t_end = (kf - k0) / FPS, (k1 - kf) / FPS
            s, alpha = 1.0, 255
            if ui.fx_on and k0 > 0 and t < 0.3:          # 冒出时的弹出动画
                s = 0.55 + 0.57 * t / 0.12 if t < 0.12 else 1.12 - 0.12 * (t - 0.12) / 0.18
            elif ui.fx_on and lfx.bubble_mode == "few" and k1 < self.n_frames - 1 and t_end < 0.25:   # 收起
                s, alpha = 0.7 + 0.3 * max(0.0, t_end) / 0.25, int(255 * max(0.0, t_end) / 0.25)
            stack.append((key, bfs, s, alpha))
        # 头顶放得下就叠在头顶；放不下（叠放布局里人靠近上沿）就挂到头的右侧，不压脸
        gap = 3
        sizes = [(fx.bubble_sprite(it[0], it[1], color) if isinstance(it, tuple) else it).get_size() for it in stack]
        tot = sum(h + gap for _w, h in sizes)
        side = None
        if c[1] - R - 3 - tot < rect.y + 2:
            maxw = max((w for w, _h in sizes), default=0) + 8
            y = max(rect.y + 2, min(rect.bottom - tot - 2, c[1] - tot / 2))
            right = pygame.Rect(int(c[0] + R + 4), int(y), maxw, int(tot))
            wave = self._wave_rect(rect) if ui.show_hud else None
            side = "right"
            if right.right > rect.right - 2 or (wave and right.colliderect(wave)):
                side = "left"                       # 右边是关节图/出界：挂到头的左边
        else:
            y = c[1] - R - 3
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
                bx = min(rect.right - full_w - 2, c[0] + R + 4)
                surf.blit(img, (bx, y + (full_h - img.get_height()) / 2))
                y += full_h + gap
            elif side == "left":
                bx = max(rect.x + 2, c[0] - R - 4 - full_w)
                surf.blit(img, (bx + full_w - img.get_width(), y + (full_h - img.get_height()) / 2))
                y += full_h + gap
            else:
                bx = max(rect.x + 2, min(rect.right - img.get_width() - 2, c[0] - img.get_width() / 2))
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
                surf.blit(pill, (sx - pill.get_width() / 2, max(rect.y + 2, sy)))
                break

    def _draw_finish_flash(self, surf, rect, lane, k, fr=0.0):
        fk = lane["finish_k"]
        if fk is None or not (fk <= k < fk + 150):
            return
        t = (k + fr - fk) / FPS
        if t < 0.35:
            a = int(140 * (1 - t / 0.35)) // 10 * 10
            if a > 0:
                surf.blit(fx.panel(rect.w, rect.h, (255, 255, 255), a, 0), rect.topleft)
        s = 0.5 + 0.6 * t / 0.15 if t < 0.15 else 1.1 - 0.1 * min(1.0, (t - 0.15) / 0.2)
        fs = max(18, min(40, int(rect.h * 0.11)))
        img = text(f"冲线！{lane['result']['finish_time']:.2f}s", fs, (120, 40, 0), True)
        bw, bh = img.get_width() + 36, img.get_height() + 14
        ban = pygame.Surface((bw, bh), pygame.SRCALPHA)
        pygame.draw.rect(ban, GOLD, ban.get_rect(), border_radius=bh // 2)
        pygame.draw.rect(ban, (255, 255, 255), ban.get_rect(), 3, border_radius=bh // 2)
        ban.blit(img, (18, 7))
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
        f = lane["frames"][k]
        color = LANE_COLORS[i]
        if rect.h < 120 or rect.w < 380:
            card = hud.strip_card(surf, rect, i + 1, color, lane["name"], f["score"], max(12, min(15, int(rect.h * 0.12))))
            tag_fs = 12
        else:
            tier = "full" if rect.h >= 220 else "mid"
            card = hud.lane_card(surf, rect, i + 1, color, lane["name"], lane["model"], lane.get("effort", ""),
                                 f["score"], f["score"] if "jump" in lane else f["x"], f["contact"], tier)
            tag_fs = 14 if tier == "full" else 12
        tags = []
        if lane["down"][k]:
            tags.append(("倒地 · 不计分", DOWN_RED))
        if lane["finish_k"] is not None and k >= lane["finish_k"]:
            tags.append((f"已冲线 {lane['result']['finish_time']:.2f}s", (205, 140, 0)))
        if "jump" in lane:
            jr = lane["jump"]
            if k < lane["judge_k"]:
                tags.append((f"距起跳线 {f['dist_to_line']:+.2f}m · {f['vx']:.2f}m/s", (90, 100, 120)))
            elif jr["foul"]:
                tags.append((f"犯规：{jr['foul']['part']}过线触地", DOWN_RED))
            elif not jr["landed"]:
                tags.append(("未起跳", (90, 100, 120)))
            else:
                tags.append((f"腾空 {jr['flight_time']:.2f}s · {jr['ref_part']}计量", (205, 140, 0)))
        if lane["load_error"]:
            tags.append((f"载入失败：{lane['load_error'][:40]}", DOWN_RED))
        elif lane["result"]["errors"] and k >= lane["result"]["errors"][0][0] * FPS:
            tags.append(("act() 报错，沿用上一帧动作", DOWN_RED))
        ty = card.y + 4
        for s_, col in tags:                     # 状态标签挂在卡片右侧
            img = hud.status_tag(s_, tag_fs, col)
            surf.blit(img, (card.right + 6, ty))
            ty += img.get_height() + 4
        if rect.w >= 600 and rect.h >= 140:
            self._draw_wave(surf, rect, lane, k, fr, ui)

    @staticmethod
    def _wave_rect(rect):
        """关节波形面板（含图例条）的位置；没有面板时返回 None。与 _draw_wave 的几何一致。"""
        if rect.w < 600 or rect.h < 140:
            return None
        ww = 260 if rect.w >= 1000 else 190
        return pygame.Rect(rect.right - ww - 8, rect.y + 6, ww, max(46, min(96, int(rect.h * 0.28))) + 15)

    def _draw_wave(self, surf, rect, lane, k, fr, ui):
        """关节波形：采样点固定在整数帧上，横坐标按 kf 连续平移；帧间时最右端补一个插值点。"""
        js = range(6) if ui.all_joints else (0, 2, 4, 5)
        ww = 260 if rect.w >= 1000 else 190
        lh = 15                                   # 图例条高度（在面板内底部）
        wh = max(46, min(96, int(rect.h * 0.28))) + lh
        wx, wy = rect.right - ww - 8, rect.y + 6
        surf.blit(fx.panel(ww, wh, (255, 255, 255), 214, 8), (wx, wy))
        wh -= lh
        pygame.draw.line(surf, (215, 218, 224), (wx + 4, wy + wh // 2), (wx + ww - 4, wy + wh // 2), 1)
        n = int(WAVE_SECONDS * FPS)
        frames = lane["frames"]
        ks = list(range(k, max(0, k - n) - 1, -2))
        kf = k + fr
        for j in js:
            pts = [(wx + ww - 2 - (kf - kk) * (ww - 4) / n, wy + wh / 2 - frames[kk]["joints"][j] * (wh / 2 - 4)) for kk in ks]
            if fr:
                a = frames[k]["joints"][j]
                pts.insert(0, (wx + ww - 2, wy + wh / 2 - (a + (frames[k + 1]["joints"][j] - a) * fr) * (wh / 2 - 4)))
            if len(pts) > 1:
                pygame.draw.lines(surf, JOINT_COLORS[j], False, pts, 2)
        lx = wx + 6
        ly = wy + wh + lh // 2
        pygame.draw.line(surf, (225, 228, 234), (wx + 4, wy + wh), (wx + ww - 4, wy + wh), 1)
        for j in js:                              # 图例：色块短线 + 关节名
            img = text(JOINT_SHORT[j], 11, shade(JOINT_COLORS[j], 0.75), True)
            pygame.draw.line(surf, JOINT_COLORS[j], (lx, ly), (lx + 8, ly), 3)
            surf.blit(img, (lx + 10, ly - img.get_height() // 2))
            lx += img.get_width() + 18
        tail = "关节角 · 3 秒" + (" · J 全部" if not ui.all_joints and ui.interactive else "")
        img = text(tail, 10, (140, 146, 160))
        if lx + img.get_width() < wx + ww - 6:
            surf.blit(img, (wx + ww - 6 - img.get_width(), ly - img.get_height() // 2))

    # -- 顶栏 --
    def _badge(self, surf, x, y, s, col, fg=(255, 255, 255)):
        img = text(s, 13, fg, True)
        r = pygame.Rect(x, y, img.get_width() + 12, img.get_height() + 2)
        pygame.draw.rect(surf, col, r, border_radius=r.h // 2)
        surf.blit(img, (x + 6, y + 1))
        return r.right + 6

    def _draw_top(self, surf, k, fr, ui):
        pygame.draw.rect(surf, (20, 24, 32), (0, 0, W, TOP))
        title = f"{physics.TRACKS[self.track]['label']} · {fx.MODE_LABEL.get(self.mode, self.mode)}"
        surf.blit(text(title, 18, (240, 242, 248), True), (14, 5))
        st = text(f"{k / FPS:5.2f}s / {self.sim_time:g}s", 15, (205, 210, 222))
        surf.blit(st, (14, 33))
        x = 22 + st.get_width()
        x = self._badge(surf, x, 35, f"{round(ui.speed, 2):g}x", (70, 80, 100))     # 渐变中的倍速取两位小数
        if ui.paused:
            x = self._badge(surf, x, 35, "暂停", (190, 60, 60))
        if ui.slow_active:
            x = self._badge(surf, x, 35, "慢动作", (130, 80, 200))
        if ui.follow:
            x = self._badge(surf, x, 35, "追拍", (40, 140, 120))
        if ui.show_hud and ui.interactive:
            surf.blit(text("H 帮助", 12, (120, 128, 145)), (x + 2, 37))
        self._draw_progress(surf, k, fr)
        self._draw_timeline(surf, k + fr, ui)

    def _draw_progress(self, surf, k, fr=0.0):
        mx0, mx1, my = 430, W - 46, 46
        span = physics.TRACKS[self.track]["line"] + 5.0 if "line" in physics.TRACKS[self.track] else physics.TRACK_LENGTH
        zones = physics.TRACKS[self.track]["zones"]
        for (za, zn), (zb, _n) in zip(zones, zones[1:]):
            a, b = mx0 + (mx1 - mx0) * za / span, mx0 + (mx1 - mx0) * zb / span
            pygame.draw.line(surf, mix(fx.ZONE_COLORS.get(zn, (120, 120, 120)), (20, 24, 32), 0.35), (a, my), (b, my), 5)
        for m in range(0, int(span) + 1, 2):
            x = mx0 + (mx1 - mx0) * m / span
            pygame.draw.line(surf, (110, 118, 132), (x, my + 3), (x, my + 6), 1)
            img = text(str(m), 10, (150, 156, 170))
            surf.blit(img, (x - img.get_width() // 2, my + 6))
        pygame.draw.line(surf, (220, 220, 225), (mx1, my), (mx1, my - 30), 2)       # 终点旗
        for j in range(3):
            for row in range(2):
                pygame.draw.rect(surf, (20, 20, 20) if (j + row) % 2 else (250, 250, 250), (mx1 + 1 + j * 5, my - 30 + row * 5, 5, 5))
        # 选手标记：赛道号圆牌（与选手卡徽章一致）。真实位置在轨道上画彩色刻度；
        # 靠得太近的圆牌并成一排（按位置从左到右），不再上下叠罗汉
        marks = sorted(((mx0 + (mx1 - mx0) * max(-0.6, min(span + 0.6, frame_val(L["frames"], k, fr, "x"))) / span, i)
                        for i, L in enumerate(self.lanes)))
        rr, step = 8, 18
        clusters = []
        for x, i in marks:
            if clusters and x - clusters[-1][-1][0] < step:
                clusters[-1].append((x, i))
            else:
                clusters.append([(x, i)])
        for x, i in marks:
            pygame.draw.line(surf, (255, 255, 255), (x, my - 5), (x, my + 3), 4)
            pygame.draw.line(surf, LANE_COLORS[i], (x, my - 4), (x, my + 2), 2)
        prev_right = -1e9
        for cl in clusters:
            cx = sum(x for x, _i in cl) / len(cl)
            w = len(cl) * step
            left = max(mx0 - 10, min(mx1 + 6 - w, cx - w / 2), prev_right + 2)
            prev_right = left + w
            y = my - 15
            for j, (x, i) in enumerate(cl):
                bx = int(left + step / 2 + j * step)
                if abs(bx - x) > 3:
                    pygame.draw.line(surf, LANE_COLORS[i], (bx, y + rr - 1), (x, my - 5), 1)
                hud.lane_badge(surf, (bx, y), rr, i + 1, LANE_COLORS[i])

    def _draw_timeline(self, surf, k, ui):
        r = self.timeline_rect()
        pygame.draw.rect(surf, (55, 62, 76), r, border_radius=4)
        frac = k / max(1, self.n_frames - 1)
        pygame.draw.rect(surf, (230, 190, 70), (r.x, r.y, max(4, int(r.w * frac)), r.h), border_radius=4)
        for a, b in self.slow_windows:
            xa = r.x + r.w * max(0, a) / (self.n_frames - 1)
            xb = r.x + r.w * min(self.n_frames - 1, b) / (self.n_frames - 1)
            pygame.draw.line(surf, (150, 110, 220), (xa, r.bottom + 2), (xb, r.bottom + 2), 2)
        for ke, i, kind in self.events:
            x = r.x + r.w * ke / (self.n_frames - 1)
            y = r.y - 3
            col = LANE_COLORS[i]
            if kind == "fall":
                pygame.draw.polygon(surf, col, [(x - 4, y - 6), (x + 4, y - 6), (x, y)])
                pygame.draw.polygon(surf, (255, 255, 255), [(x - 4, y - 6), (x + 4, y - 6), (x, y)], 1)
            elif kind == "hurdle":
                pygame.draw.polygon(surf, (250, 150, 40), [(x, y - 7), (x + 4, y - 3), (x, y + 1), (x - 4, y - 3)])
            else:
                pygame.draw.line(surf, (255, 255, 255), (x, y + 1), (x, y - 9), 1)
                pygame.draw.rect(surf, GOLD, (x, y - 9, 7, 5))
        px = r.x + r.w * frac
        pygame.draw.circle(surf, (255, 255, 255), (px, r.centery), 7)
        pygame.draw.circle(surf, (230, 190, 70), (px, r.centery), 7, 2)

    # -- 结束成绩榜（右侧面板，赛道区让出位置）--
    def ranked(self):
        return sorted(range(len(self.lanes)), key=lambda i: (self.lanes[i]["result"]["finish_time"] is None,
                                                             self.lanes[i]["result"]["finish_time"] or 0,
                                                             -self.lanes[i]["result"]["score"]))

    def _draw_board(self, surf, end_t):
        x0 = W - PANEL_W
        bg = fx._cached(("board_bg", PANEL_W, H - TOP), lambda: _gradient(PANEL_W, H - TOP, (30, 36, 56), (16, 18, 28)))
        surf.blit(bg, (x0, TOP))
        pygame.draw.line(surf, GOLD, (x0, TOP), (x0, H), 3)
        fx.text_shadow(surf, "比赛结束 · 成绩榜", 24, GOLD, (x0 + PANEL_W // 2, TOP + 12), bold=True, center=True)
        sub = f"{physics.TRACKS[self.track]['label']} · {fx.MODE_LABEL.get(self.mode, self.mode)} · {self.sim_time:g} 秒"
        img = text(sub, 13, (170, 178, 196))
        surf.blit(img, (x0 + (PANEL_W - img.get_width()) // 2, TOP + 46))
        n = len(self.lanes)
        avail = H - TOP - 84
        row_h = min(124, avail // n)
        y_start = TOP + 72 + (avail - row_h * n) // 3          # 卡片组略偏上居中（单人时不再贴顶留一大片空）
        for r, i in enumerate(self.ranked()):
            lane = self.lanes[i]
            res = lane["result"]
            ts = _ease((end_t - 0.15 * r) / 0.4)
            if ts <= 0:
                continue
            tc = _ease((end_t - 0.3 - 0.15 * r) / 1.2)
            card = pygame.Rect(x0 + 14 + int((1 - ts) * PANEL_W), y_start + r * row_h, PANEL_W - 28, row_h - 10)
            pygame.draw.rect(surf, (40, 46, 64), card, border_radius=10)
            pygame.draw.rect(surf, mix(LANE_COLORS[i], (40, 46, 64), 0.55), card, 1, border_radius=10)
            pygame.draw.rect(surf, LANE_COLORS[i], (card.x, card.y, 6, card.h), border_top_left_radius=10, border_bottom_left_radius=10)
            # 奖牌：彩带 + 圆牌 + 名次
            mc = (card.x + 36, card.y + 34)
            col = MEDALS[r] if r < 3 else (110, 118, 135)
            pygame.draw.polygon(surf, LANE_COLORS[i], [(mc[0] - 12, card.y + 4), (mc[0] - 3, card.y + 4), (mc[0] + 2, mc[1]), (mc[0] - 7, mc[1])])
            pygame.draw.polygon(surf, shade(LANE_COLORS[i], 0.7), [(mc[0] + 12, card.y + 4), (mc[0] + 3, card.y + 4), (mc[0] - 2, mc[1]), (mc[0] + 7, mc[1])])
            pygame.draw.circle(surf, shade(col, 0.7), mc, 19)
            pygame.draw.circle(surf, col, mc, 16)
            num = text(str(r + 1), 20, (60, 40, 10) if r < 3 else (240, 240, 245), True)
            surf.blit(num, (mc[0] - num.get_width() // 2, mc[1] - num.get_height() // 2))
            # 右上：成绩 + 评级
            sc = text(f"{res['score'] * tc:.2f}m", 28, GOLD, True)
            surf.blit(sc, (card.right - sc.get_width() - 12, card.y + 4))
            grade_txt = next(t for th, g, t in (evaluator.JUMP_GRADES if "jump" in lane else evaluator.GRADES) if g == lane["grade"])
            g = hud.cpill(f"{lane['grade']} {grade_txt}", 13, (30, 30, 40), mix(LANE_COLORS[i], (255, 255, 255), 0.5))
            surf.blit(g, (card.right - 12 - g.get_width(), card.y + 42))
            # 左：选手名 + 模型 · 强度（缩字号 / 省略号，不越界）
            tx = card.x + 64
            name_w = card.right - 12 - sc.get_width() - 10 - tx
            nm = hud.fit(lane["name"], name_w, 20, (245, 247, 252), True, min_size=15)
            surf.blit(nm, (tx, card.y + 8))
            yy = card.y + 10 + max(nm.get_height(), 26)
            ml_w = card.right - 12 - g.get_width() - 10 - tx
            ep = hud.cpill(lane["effort"], 12, (255, 255, 255), shade(LANE_COLORS[i], 0.8)) if lane.get("effort") else None
            mx = tx
            mh = 0
            if lane["model"]:
                mi = hud.fit(lane["model"], ml_w - (ep.get_width() + 6 if ep else 0), 14, (196, 204, 222), False, min_size=11)
                surf.blit(mi, (tx, yy))
                mx, mh = tx + mi.get_width() + 6, mi.get_height()
            if ep:
                surf.blit(ep, (mx, yy + max(0, (mh - ep.get_height()) // 2)))
                mh = max(mh, ep.get_height())
            yy += mh
            # 底部一行：实际距离 · 冲线 · 摔倒/撞栏次数
            fin = f"冲线 {res['finish_time']:.2f}s" if res["finish_time"] is not None else "未冲线"
            if "jump" in lane:
                fin = "犯规" if res["foul"] else ("未起跳" if not res["landed"] else f"腾空 {res['flight_time']:.2f}s")
            if lane["load_error"]:
                fin = "载入失败"
            nf, nh = len(lane["fx"].falls), len(lane["fx"].hurdle_hits)
            extra = (f" · 摔倒 {nf}" if nf else "") + (f" · 撞栏 {nh}" if nh else "")
            detail = hud.fit(fin if "jump" in lane else f"实际 {res['final']['x']:.2f}m · {fin}{extra}", card.right - 12 - tx, 13, (176, 184, 202))
            dy = card.bottom - detail.get_height() - 7
            if dy > yy + 4:
                surf.blit(detail, (tx, dy))

    # -- 悬停 / 帮助 --
    def _draw_hover(self, surf, pos, k):
        mx, my = pos
        tl = self.timeline_rect()
        if ROW_A <= my < TOP and tl.x - 8 <= mx <= tl.right + 8:
            kk = self.frame_at_x(mx)
            img = text(f"跳到 {kk / FPS:.2f}s", 13, (255, 255, 255), True)
            r = pygame.Rect(0, 0, img.get_width() + 12, img.get_height() + 4)
            r.midtop = (max(40, min(W - 40, mx)), TOP + 4)
            surf.blit(fx.panel(r.w, r.h, (20, 24, 32), 230, 6), r)
            surf.blit(img, (r.x + 6, r.y + 2))
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
        imgs = [text(s, 14, (255, 255, 255) if b else (225, 230, 240), b) for s, b in lines]
        bw = max(im.get_width() for im in imgs) + 20
        bh = sum(im.get_height() for im in imgs) + 14
        x = mx + 18 if mx + 18 + bw < W else mx - 18 - bw
        y = max(TOP + 4, min(H - bh - 4, my + 12))
        surf.blit(fx.panel(bw, bh, (20, 24, 32), 225, 8), (x, y))
        pygame.draw.rect(surf, LANE_COLORS[i], (x, y, bw, bh), 2, border_radius=8)
        yy = y + 7
        for im in imgs:
            surf.blit(im, (x + 10, yy))
            yy += im.get_height()

    def _draw_help(self, surf):
        rows = [("空格", "暂停 / 继续"), ("← / →", "暂停时逐帧，播放中 ±2 秒"), ("↑ / ↓ / 滚轮", "倍速 0.25x ~ 4x"),
                (", / .", "跳到上一个 / 下一个事件（摔倒·撞栏·冲线）"), ("B", "慢动作 0.25x 开关"), ("F", "追拍：自动放大领先者"),
                ("G", "切换 叠放 / 四宫格"), ("J", "关节波形：4 条 / 全部 6 条"), ("TAB", "显示 / 隐藏 HUD"),
                ("S", "截图保存到当前文件夹"), ("R", "从头重播"), ("H", "显示 / 隐藏本帮助"), ("Q / Esc", "退出"),
                ("鼠标拖动时间轴", "跳到任意时刻"), ("鼠标点击赛道", "放大该赛道（再点还原）"), ("鼠标悬停赛道", "查看精确数值")]
        bw, rh = 620, 26
        bh = 70 + len(rows) * rh
        x, y = (W - bw) // 2, TOP + (H - TOP - bh) // 2
        surf.blit(fx.panel(bw, bh, (18, 22, 30), 235, 12), (x, y))
        pygame.draw.rect(surf, GOLD, (x, y, bw, bh), 2, border_radius=12)
        fx.text_shadow(surf, "操作说明", 22, GOLD, (x + bw // 2, y + 14), bold=True, center=True)
        for j, (key, desc) in enumerate(rows):
            yy = y + 56 + j * rh
            surf.blit(text(key, 15, (255, 230, 150), True), (x + 30, yy))
            surf.blit(text(desc, 15, (230, 234, 242)), (x + 200, yy))

    # -- 片头 --
    def draw_intro(self, surf, t):
        bg = fx._cached(("intro_bg",), lambda: _gradient(W, H, (22, 30, 60), (60, 26, 70)))
        surf.blit(bg, (0, 0))
        cx, cy = W // 2, 250
        for j in range(14):          # 旋转的放射光
            a = j * math.pi / 7 + t * 0.25
            pygame.draw.polygon(surf, (40, 46, 84) if j % 2 else (32, 38, 72),
                                [(cx, cy), (cx + math.cos(a) * 1500, cy + math.sin(a) * 1500),
                                 (cx + math.cos(a + 0.22) * 1500, cy + math.sin(a + 0.22) * 1500)])
        e = _ease(t / 0.5)
        fx.text_shadow(surf, "QWOP AI 运动会", 64, GOLD, (cx, int(90 - (1 - e) * 140)), bold=True, center=True, shadow=(60, 30, 0))
        sub = f"{physics.TRACKS[self.track]['label']} · {fx.MODE_LABEL.get(self.mode, self.mode)} · {self.sim_time:g} 秒 · 终点 {physics.TRACK_LENGTH:g} 米"
        if self.track == "jump":
            sub = f"跳远 · {fx.MODE_LABEL.get(self.mode, self.mode)} · 助跑 {physics.TRACKS['jump']['line']:g} 米 · 每跳限时 {evaluator.JUMP_TIME:g} 秒"
        e2 = _ease((t - 0.25) / 0.4)
        if e2 > 0:
            img = text(sub, 24, (230, 234, 245))
            img.set_alpha(int(255 * e2))
            surf.blit(img, (cx - img.get_width() // 2, 180))
        n = len(self.lanes)
        cw = min(280, (W - 80) // n - 20)
        total = n * cw + (n - 1) * 20
        for i, lane in enumerate(self.lanes):
            ei = _ease((t - 0.5 - 0.15 * i) / 0.45)
            if ei <= 0:
                continue
            r = pygame.Rect((W - total) // 2 + i * (cw + 20), int(372 + (1 - ei) * 260), cw, 180)   # 原 330，下移给站在卡片上的小人让位
            pygame.draw.rect(surf, shade(LANE_COLORS[i], 0.55), r.move(0, 6), border_radius=16)
            pygame.draw.rect(surf, LANE_COLORS[i], r, border_radius=16)
            pygame.draw.rect(surf, (255, 255, 255), r, 3, border_radius=16)
            # 选手造型亮相：站在卡片上沿（第 0 帧的站姿，脚底 = 物理地面 y≈0.02m）
            sc = min(80.0, cw / 2.4)
            stand = type("V", (), {})()
            stand.scale = sc
            stand.ax = r.centerx - lane["poses"][0][0][0] / PPM * sc
            stand.ay = r.y + 1
            pygame.draw.ellipse(surf, shade(LANE_COLORS[i], 0.6), (r.centerx - sc * 0.4, r.y - 3, sc * 0.8, 7))
            viz_runner.draw_runner(surf, stand, lane["poses"][0], LANE_COLORS[i], "grin", t * FPS,
                                   lane_i=i, label=self.initials[i])
            fx.text_shadow(surf, f"第 {i + 1} 道", 18, (255, 255, 255), (r.centerx, r.y + 14), center=True, shadow=shade(LANE_COLORS[i], 0.5))
            name = lane["name"]
            fs = 34
            while fs > 16 and text(name, fs, (255, 255, 255), True).get_width() > cw - 20:
                fs -= 2
            fx.text_shadow(surf, name, fs, (255, 255, 255), (r.centerx, r.y + 50), bold=True, center=True, shadow=shade(LANE_COLORS[i], 0.4))
            if lane["model"]:        # 模型名：缩字号/省略号，不超出卡片
                fg = hud.fit(lane["model"], cw - 24, 20, (255, 245, 220), True, min_size=13)
                sh = hud.fit(lane["model"], cw - 24, 20, shade(LANE_COLORS[i], 0.4), True, min_size=13)
                mx = r.centerx - fg.get_width() // 2
                surf.blit(sh, (mx + 1, r.y + 107))
                surf.blit(fg, (mx, r.y + 106))
            if lane.get("effort"):
                ep = hud.cpill(lane["effort"], 16, shade(LANE_COLORS[i], 0.45), (255, 255, 255))
                surf.blit(ep, (r.centerx - ep.get_width() // 2, r.y + 140))
        if n > 1 and t > 1.0:
            img = text("预备 · 跑！", 30, GOLD, True)
            surf.blit(img, (cx - img.get_width() // 2, 594))


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


def interactive(renderer, ui, speed, auto_slowmo):
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("QWOP AI 运动会 · 回放")
    clock = pygame.time.Clock()
    app = App(renderer, ui, speed, auto_slowmo)
    ui.interactive = True
    while True:
        dt = clock.tick(FPS) / 1000.0
        for ev in pygame.event.get():
            if not app.handle(ev):
                return
        app.tick(dt)
        app.draw(screen)
        pygame.display.flip()


# ------------------------------------------------------------ 导出 --------
def _intro_frame(renderer, surf, j, ui):
    t = j / FPS
    renderer.draw_intro(surf, t)
    fade = INTRO_SECONDS - 0.35
    if t > fade:                       # 最后 0.35 秒淡入比赛第一帧
        race = pygame.Surface((W, H))
        renderer.draw(race, 0, ui)
        race.set_alpha(int(255 * (t - fade) / 0.35))
        surf.blit(race, (0, 0))


EXPORT_QUALITY = "normal"   # normal: crf20/medium；high: crf12/slow/tune animation（画面更干净，文件约 2~3 倍）
EXPORT_COVER = None         # --cover 秒：导出视频的第一帧（封面）用回放里该时刻的画面，之后正常放片头/比赛
EXPORT_COVER_FRAMES = 4
EXPORT_BGM = None           # 背景音乐文件（--bgm）；导出后混入，循环铺满、1 秒淡入、结尾淡出
EXPORT_BGM_VOL = 0.35
EXPORT_UPSCALE = 0          # 0=原尺寸 1280x720；1080/1440=用 lanczos 放大到该高度（平台二次压缩时更不糊）


def save_video(renderer, out, ui, speed, auto_slowmo=False, intro=False):
    surf = pygame.Surface((W, H))
    enc = ["-crf", "20", "-preset", "medium"] if EXPORT_QUALITY == "normal" else ["-crf", "12", "-preset", "slow", "-tune", "animation"]
    vf = ["-vf", f"scale={EXPORT_UPSCALE * W // H}:{EXPORT_UPSCALE}:flags=lanczos"] if EXPORT_UPSCALE else []
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
    last = renderer.n_frames - 1
    pos, mult = 0.0, 1.0
    while True:
        # 每个输出帧（1/60 秒）采样一次回放位置；慢动作/非整数倍速时画相邻两帧之间的插值画面，
        # 进出慢动作窗口时倍率在 SLOW_RAMP 秒内渐变
        pos = min(float(last), pos)
        k = int(pos)
        slow = auto_slowmo and renderer.in_slow_window(k)
        mult = ramp_mult(mult, SLOW if slow else 1.0, 1.0 / FPS)
        ui.speed, ui.slow_active = speed * mult, slow
        renderer.draw(surf, pos, ui)
        emit()
        if pos >= last:
            break
        pos += ui.speed
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
    if track == "jump":
        for i, path in enumerate(bots):
            raw = names[i] if i < len(names) else os.path.splitext(os.path.basename(path))[0]
            name, model, effort = hud.parse_entry(raw)
            ls = run_jump(path, name, mode, model, effort)
            if len(bots) == 1:
                lanes += ls                 # 一个选手：三次试跳各一道
            else:                           # 多个选手：每人只放最好的一跳
                best = max(ls, key=lambda L: L["result"]["score"])
                best["name"] = f"{name} · 最好第{best['jump']['attempt']}跳"
                lanes.append(best)
        pad_lanes(lanes)
        attach_fx(lanes, track, bubbles)
        return lanes
    for i, path in enumerate(bots):
        raw = names[i] if i < len(names) else os.path.splitext(os.path.basename(path))[0]
        name, model, effort = hud.parse_entry(raw)
        lanes.append(run_bot(path, name, mode, sim_time, track, model,
                              start=(starts[i % len(starts)] if starts else None), effort=effort))
    attach_fx(lanes, track, bubbles)
    return lanes


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
    ap.add_argument("--track", choices=sorted(physics.TRACKS), default=physics.DEFAULT_TRACK, help="obstacle=障碍赛, flat=无障碍, jump=跳远（1 个 bot 时三次试跳各一道；多个 bot 时每人放最好一跳）")
    ap.add_argument("--mode", choices=sorted(physics.MODES), default="normal")
    ap.add_argument("--jump-rules", choices=sorted(physics.JUMP_RULES), default=physics.JUMP_DEFAULT_RULES,
                    help="跳远规则：v1=起跳线 9m 3 跳，v2=起跳线 3m 5 跳，v3=起跳线 5m 5 跳")
    ap.add_argument("--jump-body", choices=list(physics.JUMP_BODIES), default=physics.JUMP_DEFAULT_BODY,
                    help="跳远身体改动（主办方测试用；默认与 evaluator 相同）")
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
    ap.add_argument("--bgm", nargs="?", const="assets/bgm.m4a", default=None, help="导出时混入背景音乐（不带路径=自带合成曲 assets/bgm.m4a，可换成自己的 mp3/m4a）")
    ap.add_argument("--bgm-volume", type=float, default=0.35, help="背景音乐音量 0~1（默认 0.35）")
    ap.add_argument("--cover", default=None, help="导出视频的第一帧用回放里该秒数的画面（挑一个有趣的瞬间当封面），之后正常放片头")
    ap.add_argument("--cover-frames", type=int, default=4, help="封面帧持续的帧数（60fps，默认 4 帧）")
    ap.add_argument("--follow", action="store_true", help="开局即打开追拍（F 键）")
    ap.add_argument("--auto-slowmo", action="store_true", help="摔倒前后自动 0.25x 慢动作 1.5 秒")
    ap.add_argument("--no-fx", action="store_true", help="关闭装饰特效（扬尘/震屏/碎片/速度线/彩纸/气泡动画），调试用")
    ap.add_argument("--save", help="导出 mp4（离屏渲染，不开窗口）")
    ap.add_argument("--intro", action="store_true", help="导出时加 2 秒片头（选手名 + 模型）")
    ap.add_argument("--screenshot", help="离屏渲染单帧 PNG（调试用）")
    ap.add_argument("--at", default="end", help="--screenshot 的时刻（秒）或 end")
    args = ap.parse_args()
    physics.configure_jump(args.jump_rules, args.jump_body)
    if args.track == "jump":
        jb = physics.TRACKS["jump"]["body"]
        viz_runner.set_foot(jb.get("foot_l", physics.FOOT_L), jb.get("heel_shift", physics.FOOT_HEEL_SHIFT))
    global EXPORT_QUALITY, EXPORT_UPSCALE, EXPORT_BGM, EXPORT_BGM_VOL, EXPORT_COVER, EXPORT_COVER_FRAMES
    EXPORT_COVER, EXPORT_COVER_FRAMES = args.cover, args.cover_frames
    EXPORT_QUALITY, EXPORT_UPSCALE = args.quality, args.upscale
    EXPORT_BGM, EXPORT_BGM_VOL = args.bgm, args.bgm_volume
    if len(args.bots) > 4:
        ap.error("最多 4 个 bot")

    offscreen = bool(args.save or args.screenshot)
    if offscreen:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
    pygame.init()
    if offscreen:
        pygame.display.set_mode((1, 1))          # dummy 显示，供 convert() 使用

    lanes = prepare(args.bots, args.names, args.mode, args.time, args.track,
                    args.start.split(",") if args.start else None, args.bubbles)
    if args.track == "jump":
        args.time = round(len(lanes[0]["frames"]) / FPS, 1)
    for L in lanes:
        r = L["result"]
        if "jump" in L:
            print(f"{L['name']}: 成绩 {r['score']:.2f}m" + ("（犯规）" if r["foul"] else "（未起跳）" if not r["landed"] else f"（腾空 {r['flight_time']:.2f}s）"))
            continue
        fin = f"，{r['finish_time']:.2f}s 冲线" if r["finish_time"] is not None else ""
        err = "（载入失败）" if L["load_error"] else ("（act 报错）" if r["errors"] else "")
        print(f"{L['name']}: 成绩 {r['score']:.2f}m（实际 {r['final']['x']:.2f}m）{fin}{err}")

    if not offscreen:
        pygame.display.set_mode((W, H))
    renderer = Renderer(lanes, args.mode, args.track, args.time)
    ui = UI(args.layout, not args.no_fx, args.follow)
    ui.speed = args.speed
    if args.screenshot:
        surf = pygame.Surface((W, H))
        k = _frame_at(args.at, renderer.n_frames)
        ui.end_t = 99.0 if k == renderer.n_frames - 1 else None
        renderer.draw(surf, k, ui)
        pygame.image.save(surf, args.screenshot)
        ui.end_t = None
        print(f"已保存截图 {args.screenshot}（第 {k} 帧）")
    if args.save:
        save_video(renderer, args.save, ui, args.speed, args.auto_slowmo, args.intro)
    if not offscreen:
        interactive(renderer, ui, args.speed, args.auto_slowmo)
    pygame.quit()


if __name__ == "__main__":
    main()
