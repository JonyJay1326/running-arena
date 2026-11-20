"""QWOP Arena 物理沙盒（只读，参赛 AI 不得修改）。

坐标约定：pymunk 世界单位为像素，y 轴向上；100 px = 1 m。
每个选手拥有独立的 pymunk.Space，保证无头自测与同屏渲染逐帧一致。
"""
import math

import pymunk

PPM = 100.0                 # pixels per metre
FPS = 60                    # 控制频率：act() 每秒调用 60 次
SUBSTEPS = 4                # 每个控制帧内的物理子步（物理步长 1/240 s）
DT = 1.0 / FPS
GRAVITY = -900.0
ITERATIONS = 25

KP = 20.0                   # 舵机比例增益：角速度 = KP * 角度误差
MAX_TORQUE = 350000.0       # 单个舵机最大输出力矩（像素单位）
MAX_RATE = 26.0             # 舵机最大角速度 (rad/s)

TRACK_LENGTH = 18.0         # 终点线 (m)
GROUND_END = TRACK_LENGTH + 100.0   # 地面一直铺到这里（冲线后继续跑也不会掉出世界）
SIM_TIME = 30.0             # 默认比赛时长 (s)

# 身体尺寸（px）与质量
TORSO_W, TORSO_H, TORSO_M = 20, 60, 4.41       # 质量随长度等比（68px 时 5.0）
HEAD_R, HEAD_M = 11, 1.0
THIGH_W, THIGH_H, THIGH_M = 12, 45, 1.8
SHIN_W, SHIN_H, SHIN_M = 10, 45, 0.8
FOOT_L, FOOT_H, FOOT_M = 32, 6, 0.2        # 脚掌是独立刚体，经踝关节连在小腿末端（接近真人脚：踝约在脚长 30% 处）
FOOT_HEEL_SHIFT = 6         # 脚掌中心相对踝点（小腿中线）前移量 (px)：脚跟在踝后 10px，脚尖在踝前 22px（踝约在脚长 30% 处）
# 踝关节：不受选手控制，随膝关节自动联动。每个物理子步的目标背屈角
#   = min(ANKLE_LIMITS 上限, ANKLE_COUPLE × (膝屈曲 − 5°))，膝伸直时脚掌与小腿垂直。
ANKLE_COUPLE = 0.65
ANKLE_LIMITS = (math.radians(-30), math.radians(60))
ANKLE_TORQUE = 350000.0

ARM_W, ARM_H, ARM_M = 10, 35, 0.4          # 上臂
FORE_W, FORE_H, FORE_M = 8, 45, 0.3        # 前臂（含手），肘部固定微弯
SHOULDER_DROP = 10                         # 肩关节在躯干顶端下方的距离 (px)
ELBOW_BEND = math.radians(25)              # 肘部锁定弯曲角（前臂相对上臂向前）
SHOULDER_TORQUE = 30000.0                  # 肩舵机最大力矩，明显小于髋/膝，防止"手脚并用爬行流"
HAND_FRICTION = 0.05                       # 手/前臂摩擦（与地面摩擦之积），低摩擦撑地也推不动

HIP_LIMITS = (math.radians(-60), math.radians(75))
KNEE_LIMITS = (math.radians(-110), math.radians(-5))
SHOULDER_LIMITS = (math.radians(-70), math.radians(160))   # 0° = 手臂自然下垂（归一化约 -0.39）
N_ACTIONS = 6               # [hip_L, knee_L, hip_R, knee_R, shoulder_L, shoulder_R]
REST_ACTION = [0.0, 1.0, 0.0, 1.0, -0.39, -0.39]

GROUP = 1                   # 同一小人的所有部件互不碰撞

MODES = {
    # name: (gravity, ground friction)
    "normal": (GRAVITY, 1.0),
    "ice": (GRAVITY, 0.12),
    "moon": (GRAVITY * 0.5, 1.0),
}
FOOT_FRICTION = 1.5         # pymunk 实际摩擦 = 两接触面摩擦之积
BODY_FRICTION = {"torso": 2.0, "thigh": 1.6, "shin": 0.8}


# ---------------------------------------------------------------- 赛道 ----
# 基础地形折线 (m)：0~5 平地，5~9 减速带，9~11 15°上坡，11~13 15°下坡，13~18 跨栏
_SLOPE_H = 2.0 * math.tan(math.radians(15))
TERRAIN = [(-10.0, 0.0), (9.0, 0.0), (11.0, _SLOPE_H), (13.0, 0.0), (GROUND_END, 0.0)]
BUMPS = [(x, 0.08, 0.30) for x in (5.5, 6.5, 7.5, 8.5)]             # (中心 x, 高, 宽) 固定
HURDLES = [(x, 0.20, 0.04) for x in (14.0, 15.5, 17.0)]             # 可被踢倒的动态栏架
ZONES = [(0.0, "平地"), (5.0, "减速带"), (9.0, "上坡"), (11.0, "下坡"), (13.0, "跨栏"), (TRACK_LENGTH, "终点")]


# 两条赛道：obstacle = 障碍赛（上面的地形+减速带+跨栏）；flat = 无障碍纯平地冲刺
TRACKS = {
    "obstacle": {"terrain": TERRAIN, "bumps": BUMPS, "hurdles": HURDLES, "zones": ZONES, "label": "障碍赛"},
    "flat": {"terrain": [(-10.0, 0.0), (GROUND_END, 0.0)], "bumps": [], "hurdles": [],
             "zones": [(0.0, "平地"), (TRACK_LENGTH, "终点")], "label": "无障碍"},
}
DEFAULT_TRACK = "obstacle"

# 起身测试的两种倒地起点：先用下面的固定动作"摔"3 秒（不计入比赛时间），再把时间清零交给选手。
FALL_ACTIONS = {
    "prone": [0.965, -0.409, 0.193, -0.1, -0.373, -0.874],     # 面朝下趴着
    "supine": [-0.731, 0.695, 0.528, -0.49, -0.009, -0.101],   # 仰躺（后脑勺着地）
}


def terrain_height(x_m, track=DEFAULT_TRACK):
    """基础地形高度（不含减速带/跨栏），单位 m。"""
    terrain = TRACKS[track]["terrain"]
    for (x0, y0), (x1, y1) in zip(terrain, terrain[1:]):
        if x0 <= x_m <= x1:
            return y0 + (y1 - y0) * (x_m - x0) / (x1 - x0)
    return 0.0


def obstacles(track=DEFAULT_TRACK):
    """所有障碍按位置排序：[(x_m, height_m, kind)]。"""
    t = TRACKS[track]
    obs = [(x, h, "bump") for x, h, _ in t["bumps"]] + [(x, h, "hurdle") for x, h, _ in t["hurdles"]]
    return sorted(obs)


# --------------------------------------------------------------- 小人 ----
def _box(body, w, h, offset=(0, 0)):
    ox, oy = offset
    verts = [(ox - w / 2, oy - h / 2), (ox + w / 2, oy - h / 2), (ox + w / 2, oy + h / 2), (ox - w / 2, oy + h / 2)]
    return pymunk.Poly(body, verts, radius=1.0)


def _limb(mass, w, h, pos):
    body = pymunk.Body(mass, pymunk.moment_for_box(mass, (w, h)))
    body.position = pos
    return body


class Runner:
    """一个独立物理世界中的 11 刚体小人：4 个腿关节 + 2 个肩关节（肘部固定）+ 2 个随膝联动的踝关节（不可控制）。"""

    JOINT_NAMES = ("hip_L", "knee_L", "hip_R", "knee_R", "shoulder_L", "shoulder_R")

    def __init__(self, mode="normal", track=DEFAULT_TRACK, start=None):
        gravity, ground_friction = MODES[mode]
        self.mode = mode
        self.track = track
        self.space = pymunk.Space()
        self.space.gravity = (0, gravity)
        self.space.iterations = ITERATIONS
        self.time = 0.0
        self.frame = 0

        self._build_ground(ground_friction)
        self._build_body()
        self._prev_x = self.torso.position.x
        if start:                       # 倒地起点：预滚 3 秒后清零计时
            for _ in range(180):
                self.step(FALL_ACTIONS[start])
            self.time = 0.0
            self.frame = 0
            self._prev_x = self.torso.position.x

    # -- 构建 --------------------------------------------------------------
    def _build_ground(self, friction):
        static = self.space.static_body
        self.ground_shapes = []
        for (x0, y0), (x1, y1) in zip(TRACKS[self.track]["terrain"], TRACKS[self.track]["terrain"][1:]):
            seg = pymunk.Segment(static, (x0 * PPM, y0 * PPM), (x1 * PPM, y1 * PPM), 2.0)
            self.ground_shapes.append(seg)
        # 起点后方 3m 处的挡墙，防止倒车选手掉出世界
        self.ground_shapes.append(pymunk.Segment(static, (-3.0 * PPM, 0), (-3.0 * PPM, 4.0 * PPM), 4.0))
        for x, h, w in TRACKS[self.track]["bumps"]:
            self.ground_shapes.append(_box(static, w * PPM, h * PPM, (x * PPM, h * PPM / 2)))
        for s in self.ground_shapes:
            s.friction = friction
        self.space.add(*self.ground_shapes)

        self.hurdles = []
        for x, h, w in TRACKS[self.track]["hurdles"]:
            base = terrain_height(x, self.track) * PPM
            body = _limb(0.6, w * PPM, h * PPM, (x * PPM, base + h * PPM / 2))
            shape = _box(body, w * PPM, h * PPM)
            shape.friction = friction
            self.space.add(body, shape)
            self.hurdles.append(body)

    def _build_body(self):
        flt = pymunk.ShapeFilter(group=GROUP)
        leg_len = THIGH_H + SHIN_H
        hip_y = leg_len + FOOT_H + 2
        torso_y = hip_y + TORSO_H / 2

        self.torso = _limb(TORSO_M, TORSO_W, TORSO_H, (0, torso_y))
        torso_shape = _box(self.torso, TORSO_W, TORSO_H)
        head = pymunk.Circle(self.torso, HEAD_R, (0, TORSO_H / 2 + HEAD_R))
        self.torso.mass = TORSO_M + HEAD_M
        for s in (torso_shape, head):
            s.friction = BODY_FRICTION["torso"]
        self.torso_shapes = [torso_shape, head]
        self.space.add(self.torso, torso_shape, head)

        self.thighs, self.shins, self.feet, self.motors = [], [], [], []
        self.leg_shapes = []
        self.foot_bodies, self.ankle_motors = [], []
        for _side in (0, 1):
            thigh = _limb(THIGH_M, THIGH_W, THIGH_H, (0, hip_y - THIGH_H / 2))
            shin = _limb(SHIN_M, SHIN_W, SHIN_H, (0, hip_y - THIGH_H - SHIN_H / 2))
            thigh_s = _box(thigh, THIGH_W, THIGH_H)
            thigh_s.friction = BODY_FRICTION["thigh"]
            shin_s = _box(shin, SHIN_W, SHIN_H)
            shin_s.friction = BODY_FRICTION["shin"]
            ank_y = hip_y - THIGH_H - SHIN_H
            foot_b = _limb(FOOT_M, FOOT_L, FOOT_H, (FOOT_HEEL_SHIFT, ank_y - FOOT_H / 2 + 1))
            foot_s = _box(foot_b, FOOT_L, FOOT_H)
            foot_s.friction = FOOT_FRICTION
            self.space.add(thigh, shin, thigh_s, shin_s, foot_b, foot_s)
            ankle_m = pymunk.SimpleMotor(shin, foot_b, 0.0)
            ankle_m.max_force = ANKLE_TORQUE
            self.space.add(pymunk.PivotJoint(shin, foot_b, (0, ank_y)),
                           pymunk.RotaryLimitJoint(shin, foot_b, *ANKLE_LIMITS), ankle_m)
            self.foot_bodies.append(foot_b)
            self.ankle_motors.append((ankle_m, thigh, shin, foot_b))

            hip = pymunk.PivotJoint(self.torso, thigh, (0, hip_y))
            knee = pymunk.PivotJoint(thigh, shin, (0, hip_y - THIGH_H))
            hip_lim = pymunk.RotaryLimitJoint(self.torso, thigh, *HIP_LIMITS)
            knee_lim = pymunk.RotaryLimitJoint(thigh, shin, *KNEE_LIMITS)
            hip_m = pymunk.SimpleMotor(self.torso, thigh, 0.0)
            knee_m = pymunk.SimpleMotor(thigh, shin, 0.0)
            for m in (hip_m, knee_m):
                m.max_force = MAX_TORQUE
            self.space.add(hip, knee, hip_lim, knee_lim, hip_m, knee_m)

            self.thighs.append(thigh)
            self.shins.append(shin)
            self.feet.append(foot_s)
            self.motors += [(hip_m, self.torso, thigh, HIP_LIMITS), (knee_m, thigh, shin, KNEE_LIMITS)]
            self.leg_shapes += [thigh_s, shin_s]

        self.arms, self.forearms = [], []
        sh_y = hip_y + TORSO_H - SHOULDER_DROP
        for _side in (0, 1):
            arm = _limb(ARM_M, ARM_W, ARM_H, (0, sh_y - ARM_H / 2))
            elbow_y = sh_y - ARM_H
            fore = _limb(FORE_M, FORE_W, FORE_H, (0, 0))
            fore.angle = ELBOW_BEND
            # 前臂中心 = 肘点 + 绕肘点旋转后的 (0, -FORE_H/2)
            fore.position = (math.sin(ELBOW_BEND) * FORE_H / 2, elbow_y - math.cos(ELBOW_BEND) * FORE_H / 2)
            arm_s = _box(arm, ARM_W, ARM_H)
            fore_s = _box(fore, FORE_W, FORE_H)
            arm_s.friction = fore_s.friction = HAND_FRICTION
            self.space.add(arm, fore, arm_s, fore_s)

            shoulder = pymunk.PivotJoint(self.torso, arm, (0, sh_y))
            elbow = pymunk.PivotJoint(arm, fore, (0, elbow_y))
            shoulder_lim = pymunk.RotaryLimitJoint(self.torso, arm, *SHOULDER_LIMITS)
            elbow_lock = pymunk.RotaryLimitJoint(arm, fore, ELBOW_BEND, ELBOW_BEND)
            shoulder_m = pymunk.SimpleMotor(self.torso, arm, 0.0)
            shoulder_m.max_force = SHOULDER_TORQUE
            self.space.add(shoulder, elbow, shoulder_lim, elbow_lock, shoulder_m)

            self.arms.append(arm)
            self.forearms.append(fore)
            self.motors.append((shoulder_m, self.torso, arm, SHOULDER_LIMITS))

        for s in self.space.shapes:
            if s.body in (self.torso, *self.thighs, *self.shins, *self.foot_bodies, *self.arms, *self.forearms):
                s.filter = flt

    # -- 传感器 ------------------------------------------------------------
    def joint_angles(self):
        """6 个关节当前归一化角度，顺序 [hip_L, knee_L, hip_R, knee_R, shoulder_L, shoulder_R]。"""
        out = []
        for _m, a, b, (lo, hi) in self.motors:
            rel = b.angle - a.angle
            out.append(max(-1.0, min(1.0, 2.0 * (rel - lo) / (hi - lo) - 1.0)))
        return out

    def _touching(self, body, shapes):
        hit = [False]
        ground = set(self.ground_shapes) | {s for h in self.hurdles for s in h.shapes}

        def check(arb):
            a, b = arb.shapes
            if (a in shapes and b in ground) or (b in shapes and a in ground):
                hit[0] = True

        body.each_arbiter(check)
        return hit[0]

    def _foot_touch(self, i):
        """foot_* = 小腿或脚掌触地。"""
        shin, foot = self.shins[i], self.foot_bodies[i]
        return self._touching(shin, set(shin.shapes)) or self._touching(foot, set(foot.shapes))

    def contacts(self):
        return {
            "foot_L": self._foot_touch(0),
            "foot_R": self._foot_touch(1),
            "torso": self._touching(self.torso, set(self.torso_shapes)),
            "hand_L": self._touching(self.forearms[0], set(self.forearms[0].shapes) | set(self.arms[0].shapes)),
            "hand_R": self._touching(self.forearms[1], set(self.forearms[1].shapes) | set(self.arms[1].shapes)),
        }

    def observe(self):
        x_m = self.torso.position.x / PPM
        nxt = next(((ox, oh) for ox, oh, _k in obstacles(self.track) if ox > x_m - 0.1), None)
        return {
            "time": round(self.time, 6),
            "x": x_m,
            "y": self.torso.position.y / PPM - terrain_height(x_m, self.track),
            "vx": self.torso.velocity.x / PPM,
            "vy": self.torso.velocity.y / PPM,
            "torso_angle": -self.torso.angle,
            "torso_omega": -self.torso.angular_velocity,
            "joints": self.joint_angles(),
            "contact": self.contacts(),
            "next_bump_dist": (nxt[0] - x_m) if nxt else 99.0,
            "next_bump_height": nxt[1] if nxt else 0.0,
        }

    # -- 控制与推进 --------------------------------------------------------
    def apply(self, action):
        if action is None or len(action) not in (4, N_ACTIONS):
            action = REST_ACTION
        elif len(action) == 4:      # 兼容仅控制腿的旧 bot，手臂保持自然下垂
            action = list(action) + REST_ACTION[4:]
        for u, (motor, a, b, (lo, hi)) in zip(action, self.motors):
            try:
                u = float(u)
            except (TypeError, ValueError):
                u = 0.0
            if not math.isfinite(u):
                u = 0.0
            u = max(-1.0, min(1.0, u))
            target = lo + (u + 1.0) / 2.0 * (hi - lo)
            err = target - (b.angle - a.angle)
            rate = max(-MAX_RATE, min(MAX_RATE, KP * err))
            motor.rate = -rate      # pymunk: b.w - a.w = -rate

    def _drive_ankles(self):
        """踝关节随膝联动（每个物理子步更新一次目标）。"""
        for motor, thigh, shin, foot in self.ankle_motors:
            flex = max(0.0, -(shin.angle - thigh.angle) - math.radians(5))
            target = min(ANKLE_LIMITS[1], ANKLE_COUPLE * flex)
            err = target - (foot.angle - shin.angle)
            motor.rate = -max(-MAX_RATE, min(MAX_RATE, KP * 2 * err))

    def step(self, action):
        self.apply(action)
        sub = DT / SUBSTEPS
        for _ in range(SUBSTEPS):
            self._drive_ankles()
            self.space.step(sub)
        self.time += DT
        self.frame += 1

    # -- 渲染用 ------------------------------------------------------------
    def parts(self):
        """[(body, shape)] 供可视化器绘制。"""
        return [(s.body, s) for s in self.space.shapes if s.body is not self.space.static_body]
