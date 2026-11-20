"""QWOP Arena 物理沙盒（只读，参赛 AI 不得修改）。

坐标约定：pymunk 世界单位为像素，y 轴向上；100 px = 1 m。
每个选手拥有独立的 pymunk.Space，保证无头自测与同屏渲染逐帧一致。
"""
import math
import hashlib

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

# 第 2 期跳远：0 ~ JUMP_LINE 平地助跑道，JUMP_LINE 处为起跳线，之后是沙坑（地面摩擦更高），
# 沙坑长 PIT_LENGTH，末端有挡墙。沙坑与助跑道同高（平地）。
JUMP_LINE = 9.0             # 起跳线 x (m)
PIT_LENGTH = 15.0           # 沙坑长度 (m)
SAND_FRICTION = 3.0         # 沙坑地面摩擦系数（助跑道 1.0；实际摩擦 = 两接触面之积）
TRACKS["jump"] = {"terrain": [(-10.0, 0.0), (JUMP_LINE, 0.0), (JUMP_LINE + PIT_LENGTH, 0.0)], "bumps": [], "hurdles": [],
                  "zones": [(0.0, "助跑道"), (JUMP_LINE, "沙坑")], "label": "跳远",
                  "line": JUMP_LINE, "pit": (JUMP_LINE, JUMP_LINE + PIT_LENGTH, SAND_FRICTION),
                  "end_wall": JUMP_LINE + PIT_LENGTH}
# 规则版本（第二轮）：v1 = 起跳线 9m、3 跳（第一轮原值）；v2 = 起跳线 3m、5 跳（助跑道缩短，沙坑长度不变、挡墙跟着前移）
# v3 = 起跳线 5m、5 跳（第三轮）
JUMP_RULES = {"v1": {"line": 9.0, "attempts": 3}, "v2": {"line": 3.0, "attempts": 5}, "v3": {"line": 5.0, "attempts": 5},
              "v4": {"line": 12.0, "attempts": 5, "free_takeoff": True, "sand_torque": 0.35, "max_start": 9.0}}
# v4：板前自选起跳位置；成绩始终从 x=12m 起跳线量，提前起跳损失距离。
# 身体改动（第二轮，只在跳远赛道生效，第 1 期常量不动）：Runner 从 TRACKS[track]["body"] 读覆盖值，没有就用模块常量
#   A：踝联动上限 60°→70°（0.65×(110°−5°)=68° 是联动能到的最大目标，70° 让它不再被截断）+ 踝力矩 35 万→70 万；脚形状不变
#   B：肩力矩 3 万→6 万 / 12 万
JUMP_BODIES = {
    "base": {},
    "A": {"ankle_hi": math.radians(70), "ankle_torque": 700000.0},
    "B60": {"shoulder_torque": 60000.0},
    "B120": {"shoulder_torque": 120000.0},
    "AB60": {"ankle_hi": math.radians(70), "ankle_torque": 700000.0, "shoulder_torque": 60000.0},
    "AB120": {"ankle_hi": math.radians(70), "ankle_torque": 700000.0, "shoulder_torque": 120000.0},
}
# 第三轮"更不容易倒"的候选（都在 A 的基础上；可用的覆盖键见 Runner.__init__）
_A = JUMP_BODIES["A"]
JUMP_BODIES.update({
    "A_heel": dict(_A, heel_shift=2),                                  # 脚长不变（32px），踝点前移：脚跟在踝后 14px、脚尖在踝前 18px
    "A_foot36": dict(_A, foot_l=36, heel_shift=7),                     # 脚长 36px（踝仍约在 30% 处）
    "A_foot40": dict(_A, foot_l=40, heel_shift=8),                     # 脚长 40px
    "A_lowcg": dict(_A, torso_cog=-15.0, foot_m=0.6),                  # 外形不变：躯干质心下移 15px、脚掌 0.2→0.6kg
    "A_stable": dict(_A, heel_shift=2, torso_cog=-15.0, foot_m=0.6),   # A_heel + A_lowcg
    "A_t4": dict(_A, max_torque=1400000.0),                            # 第四轮：髋/膝舵机力矩 35 万→140 万（×4），其余同 A；只在跳远赛道生效
    "A_t3": dict(_A, max_torque=1050000.0),
    "A_t25s6_ank": dict(_A, max_torque=875000.0, shoulder_torque=60000.0, ankle_ctrl=True, ankle_lo=math.radians(-50)),   # 第六轮：髋/膝力矩 ×2.5（87.5 万），其余同 A_t3s6_ank
    "A_t25s6_ank_nosand": dict(_A, max_torque=875000.0, shoulder_torque=60000.0, ankle_ctrl=True, ankle_lo=math.radians(-50), sand_torque=None),   # 对照：关掉触沙放松
    "A_t3s6_ank": dict(_A, max_torque=1050000.0, shoulder_torque=60000.0, ankle_ctrl=True, ankle_lo=math.radians(-50)),   # 第五轮：A_t3s6 + 踝关节单独可控（8 个输出）+ 踝下限 -50°（蹬地）；髋行程不变
    "A_t3s6": dict(_A, max_torque=1050000.0, shoulder_torque=60000.0), # 第五轮：A_t3 + 肩力矩 3 万→6 万；只在跳远赛道生效
    # 第七轮：能量衰减。space_damping = 每秒保留的速度比例；joint_damping = 关节粘性；max_rate = 舵机限速
    "A_t3s6_ank_d05": dict(_A, max_torque=1050000.0, shoulder_torque=60000.0, ankle_ctrl=True, ankle_lo=math.radians(-50), space_damping=0.95),
    "A_t3s6_ank_d10": dict(_A, max_torque=1050000.0, shoulder_torque=60000.0, ankle_ctrl=True, ankle_lo=math.radians(-50), space_damping=0.90),
    "A_t3s6_ank_d20": dict(_A, max_torque=1050000.0, shoulder_torque=60000.0, ankle_ctrl=True, ankle_lo=math.radians(-50), space_damping=0.80),
    "A_t3s6_ank_j5": dict(_A, max_torque=1050000.0, shoulder_torque=60000.0, ankle_ctrl=True, ankle_lo=math.radians(-50), joint_damping=5.0),
    "A_t3s6_ank_j10": dict(_A, max_torque=1050000.0, shoulder_torque=60000.0, ankle_ctrl=True, ankle_lo=math.radians(-50), joint_damping=10.0),
    "A_t3s6_ank_d10j5": dict(_A, max_torque=1050000.0, shoulder_torque=60000.0, ankle_ctrl=True, ankle_lo=math.radians(-50), space_damping=0.90, joint_damping=5.0),
    "A_t3s6_ank_rate15": dict(_A, max_torque=1050000.0, shoulder_torque=60000.0, ankle_ctrl=True, ankle_lo=math.radians(-50), max_rate=15.0),
    # 第七轮推荐组合：×2.5 力矩（起跳角接近人类）+ 3%/s 空气衰减 + 轻度关节粘性
    "A_t25s6_ank_d3": dict(_A, max_torque=875000.0, shoulder_torque=60000.0, ankle_ctrl=True, ankle_lo=math.radians(-50), space_damping=0.97, joint_damping=0.05),
    "A_t3s6_ank_d3": dict(_A, max_torque=1050000.0, shoulder_torque=60000.0, ankle_ctrl=True, ankle_lo=math.radians(-50), space_damping=0.97, joint_damping=0.05),
    # 第八轮：踝背屈上限 70°→25°（人类 20~25°），其余同 A_t25s6_ank_d3
    "A_t25s6_ank_d3a": dict(_A, max_torque=875000.0, shoulder_torque=60000.0, ankle_ctrl=True,
                            ankle_lo=math.radians(-50), ankle_hi=math.radians(25), space_damping=0.97, joint_damping=0.05),
    # 第九轮：全部关节改成人体活动范围（髋 -30°~+120°、膝 0°~140°、踝 -50°~+25°、肩 -60°~+175°）
    "A_t25s6_ank_h": dict(_A, max_torque=875000.0, shoulder_torque=60000.0, ankle_ctrl=True,
                          ankle_lo=math.radians(-50), ankle_hi=math.radians(25),
                          hip_limits=(math.radians(-30), math.radians(120)),
                          knee_limits=(math.radians(-140), math.radians(-5)),
                          shoulder_limits=(math.radians(-60), math.radians(175)),
                          space_damping=0.97, joint_damping=0.05),
    # 踝背屈放宽版：30° / 35°（人体膝微屈时可到 30°，35° 偏上但不算离谱）
    "A_t25s6_ank_h30": dict(_A, max_torque=875000.0, shoulder_torque=60000.0, ankle_ctrl=True,
                            ankle_lo=math.radians(-50), ankle_hi=math.radians(30),
                            hip_limits=(math.radians(-30), math.radians(120)),
                            knee_limits=(math.radians(-140), math.radians(-5)),
                            shoulder_limits=(math.radians(-60), math.radians(175)),
                            space_damping=0.97, joint_damping=0.05),
    "A_t25s6_ank_h35": dict(_A, max_torque=875000.0, shoulder_torque=60000.0, ankle_ctrl=True,
                            ankle_lo=math.radians(-50), ankle_hi=math.radians(35),
                            hip_limits=(math.radians(-30), math.radians(120)),
                            knee_limits=(math.radians(-140), math.radians(-5)),
                            shoulder_limits=(math.radians(-60), math.radians(175)),
                            space_damping=0.97, joint_damping=0.05),
    # 诊断用：同 h（踝 25°）但关掉能量衰减，看长距离跑是不是被阻尼拖垮
    "A_t25s6_ank_h_nodamp": dict(_A, max_torque=875000.0, shoulder_torque=60000.0, ankle_ctrl=True,
                                 ankle_lo=math.radians(-50), ankle_hi=math.radians(25),
                                 hip_limits=(math.radians(-30), math.radians(120)),
                                 knee_limits=(math.radians(-140), math.radians(-5)),
                                 shoulder_limits=(math.radians(-60), math.radians(175))),
    # 轻衰减候选：1%/s / 2%/s 空气 + 关节粘性 0.02
    "A_t25s6_ank_h_d1": dict(_A, max_torque=875000.0, shoulder_torque=60000.0, ankle_ctrl=True,
                             ankle_lo=math.radians(-50), ankle_hi=math.radians(25),
                             hip_limits=(math.radians(-30), math.radians(120)),
                             knee_limits=(math.radians(-140), math.radians(-5)),
                             shoulder_limits=(math.radians(-60), math.radians(175)),
                             space_damping=0.99, joint_damping=0.02),
    # 只压旋转的候选（平动不衰减）：angular_damping = 每秒保留的角速度比例
    "A_t25s6_ank_h_ad60": dict(_A, max_torque=875000.0, shoulder_torque=60000.0, ankle_ctrl=True,
                               ankle_lo=math.radians(-50), ankle_hi=math.radians(25),
                               hip_limits=(math.radians(-30), math.radians(120)),
                               knee_limits=(math.radians(-140), math.radians(-5)),
                               shoulder_limits=(math.radians(-60), math.radians(175)),
                               angular_damping=0.60),
    "A_t25s6_ank_h_ad75": dict(_A, max_torque=875000.0, shoulder_torque=60000.0, ankle_ctrl=True,
                               ankle_lo=math.radians(-50), ankle_hi=math.radians(25),
                               hip_limits=(math.radians(-30), math.radians(120)),
                               knee_limits=(math.radians(-140), math.radians(-5)),
                               shoulder_limits=(math.radians(-60), math.radians(175)),
                               angular_damping=0.75),
    "A_t25s6_ank_h_ad85": dict(_A, max_torque=875000.0, shoulder_torque=60000.0, ankle_ctrl=True,
                               ankle_lo=math.radians(-50), ankle_hi=math.radians(25),
                               hip_limits=(math.radians(-30), math.radians(120)),
                               knee_limits=(math.radians(-140), math.radians(-5)),
                               shoulder_limits=(math.radians(-60), math.radians(175)),
                               angular_damping=0.85),
    # 降弹跳：同 ad85（只压旋转，无线性阻尼），只把踝力矩 70 万→50 万 / 42 万（弹跳主要来自踝；髋/膝力矩对垂直起跳几乎没影响）
    # 回到旧行程（踝上翘 70°、膝 -110~-5、肩 -70~160）只把髋后伸 -60°→-45°；无阻尼。随机步行器可走比例 ≈ 旧身体（35%），约为 ad60 的 3 倍
    "A_t25s6_ank_hip45": dict(_A, max_torque=875000.0, shoulder_torque=60000.0, ankle_ctrl=True, ankle_lo=math.radians(-50),
                              hip_limits=(math.radians(-45), math.radians(75))),
    # 主持定的候选（2026-11-04）：旧行程基础上 髋 -45°~90°、膝 -110°~0°（能伸直，开局不再被限位器掰 5°）、踝 -50°~70°；
    # 髋/膝力矩 52.5 万（×1.5）、踝 55 万、肩 6 万；无阻尼。随机步行器可走比例约为 ad60 的 3 倍，纵跳约 1.7m
    "A_t15a55_h90k0": dict(_A, max_torque=525000.0, shoulder_torque=60000.0, ankle_ctrl=True, ankle_torque=550000.0,
                           ankle_lo=math.radians(-50),
                           hip_limits=(math.radians(-45), math.radians(90)),
                           knee_limits=(math.radians(-110), math.radians(0))),
    # 2026-11-04 主持定：A_t15a55_h90k0 + 力–速度限制（关节同向转速达到 26 rad/s 时力矩只剩一半）。纵跳约 1.0~1.2m，接近人类顶尖
    "A_t15a55_h90k0_fv50": dict(_A, max_torque=525000.0, shoulder_torque=60000.0, ankle_ctrl=True, ankle_torque=550000.0,
                                ankle_lo=math.radians(-50),
                                hip_limits=(math.radians(-45), math.radians(90)),
                                knee_limits=(math.radians(-110), math.radians(0)),
                                fv_min=0.5),
    "A_t25s6_ank_h_a50": dict(_A, max_torque=875000.0, shoulder_torque=60000.0, ankle_ctrl=True, ankle_torque=500000.0,
                              ankle_lo=math.radians(-50), ankle_hi=math.radians(25),
                              hip_limits=(math.radians(-30), math.radians(120)),
                              knee_limits=(math.radians(-140), math.radians(-5)),
                              shoulder_limits=(math.radians(-60), math.radians(175)),
                              angular_damping=0.85),
    "A_t25s6_ank_h_a42": dict(_A, max_torque=875000.0, shoulder_torque=60000.0, ankle_ctrl=True, ankle_torque=420000.0,
                              ankle_lo=math.radians(-50), ankle_hi=math.radians(25),
                              hip_limits=(math.radians(-30), math.radians(120)),
                              knee_limits=(math.radians(-140), math.radians(-5)),
                              shoulder_limits=(math.radians(-60), math.radians(175)),
                              angular_damping=0.85),
})


def set_jump_line(x):
    """挪起跳线：助跑道地面、沙坑（长度不变）、挡墙一起挪。"""
    t = TRACKS["jump"]
    t["line"], t["pit"], t["end_wall"] = x, (x, x + PIT_LENGTH, SAND_FRICTION), x + PIT_LENGTH
    t["terrain"] = [(-10.0, 0.0), (x, 0.0), (x + PIT_LENGTH, 0.0)]
    t["zones"] = [(0.0, "助跑道"), (x, "沙坑")]


def configure_jump(rules=None, body=None):
    """切换跳远规则版本（v1~v4）和身体改动（JUMP_BODIES 的键）。不传的保持不变。"""
    if rules is not None:
        set_jump_line(JUMP_RULES[rules]["line"])
        TRACKS["jump"]["attempts"] = JUMP_RULES[rules]["attempts"]
        TRACKS["jump"]["rules"] = rules
        r = JUMP_RULES[rules]
        TRACKS["jump"]["free_takeoff"] = bool(r.get("free_takeoff"))
        TRACKS["jump"]["sand_torque"] = r.get("sand_torque")     # 触沙后关节力矩倍率（None = 不限制）
        TRACKS["jump"]["max_start"] = r.get("max_start")         # v4：START_X 上限（7 = 至少 5 米助跑；None = 到板）
        if r.get("free_takeoff"):      # v4：脚尖起点0～12m，对应12～0m助跑；后方只留身体空间
            t = TRACKS["jump"]
            t["terrain"] = [(-0.5, 0.0), (t["line"], 0.0), (t["line"] + PIT_LENGTH, 0.0)]
            t["back_wall"] = -0.5
        else:
            TRACKS['jump'].pop('back_wall', None)
        if r.get("pit_start") is not None:      # v4：沙坑提前到 5m，整条道都能起跳
            t = TRACKS["jump"]
            ps, pe = r["pit_start"], r["pit_end"]
            t["terrain"] = [(-10.0, 0.0), (ps, 0.0)]
            t["pit"] = (ps, pe, SAND_FRICTION)
            t["end_wall"] = pe
            t["zones"] = [(0.0, "助跑道"), (ps, "沙坑")]
    if body is not None:
        TRACKS["jump"]["body"] = dict(JUMP_BODIES[body])
        TRACKS["jump"]["body_name"] = body


# 评测 / 回放 / 搜索工具共用的默认跳远配置（命令行 --jump-rules / --jump-body 的默认值都取这里）
# 2026-11-04 候选：踝上翘 60° + 重力 9.81 + 舵机最快 16 rad/s（压纵跳到约 1.4 m，步行不受影响）
JUMP_BODIES["n60_g_r16"] = dict(JUMP_BODIES["A_t15a55_h90k0"], ankle_hi=math.radians(60), ankle_torque=700000.0, gravity=9.81, max_rate=16.0)
JUMP_BODIES["s70_g_r16"] = dict(JUMP_BODIES["n60_g_r16"], max_torque=700000.0)                       # 加强：髋/膝 70 万
JUMP_BODIES["s70e_g_r16"] = dict(JUMP_BODIES["s70_g_r16"], ecc=1.4)                                    # 加强 + 离心 1.4 倍
JUMP_BODIES["n60e_g_r16"] = dict(JUMP_BODIES["n60_g_r16"], ecc=1.4)                                    # 只加离心
JUMP_BODIES["s875e_g_r16"] = dict(JUMP_BODIES["n60_g_r16"], max_torque=875000.0, ecc=1.4)              # 髋/膝 87.5 万 + 离心
JUMP_BODIES["o60_g_r16"] = dict(JUMP_BODIES["A_t25s6_ank"], ankle_hi=math.radians(60), gravity=9.81, max_rate=16.0)   # 旧身体风格
JUMP_DEFAULT_RULES, JUMP_DEFAULT_BODY = "v4", "s70e_g_r16"  # 2026-11-04 晚主持定：s70e_g_r16（髋膝 70 万 + 离心 1.4）+ 助跑 5～12 米、不给参考代码。之前 n60_g_r16；更早：  # 2026-11-04 主持定稿：A_t15a55_h90k0 + 力–速度 fv50（第 1 批用的是不带 fv 的 A_t15a55_h90k0）
JUMP_BODIES["trial_old60_k0"] = dict(JUMP_BODIES["A_t25s6_ank"], ankle_hi=math.radians(60), knee_limits=(math.radians(-110), 0.0))
JUMP_DEFAULT_BODY = "trial_old60_k0"
configure_jump(JUMP_DEFAULT_RULES, JUMP_DEFAULT_BODY)
SUBSTEP_CONTACTS = True     # 跳远：腾空/犯规/落地按本帧全部物理子步的接触点判定（False = 只看帧末，原型做法）

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


WIND_DEFAULT_SEED = 20261102
WIND_BASE_LIMIT = 0.6       # m/s，每跳恒定基准风的范围
WIND_GUST_LIMIT = 0.2       # m/s，平滑阵风相对基准的范围
WIND_KNOT_SECONDS = 1.5
WIND_ACCEL_PER_MPS = 0.12  # 躯干加速度 / 风速；水平外力简化，不新增速度阻尼


class WindProfile:
    def __init__(self, seed):
        self.seed = int(seed)
        self.base = self._sample('base') * WIND_BASE_LIMIT

    def _sample(self, key):
        raw = hashlib.sha256(f'{self.seed}:{key}'.encode('ascii')).digest()
        return int.from_bytes(raw[:8], 'big') / (2**64 - 1) * 2.0 - 1.0

    def at(self, seconds):
        pos = max(0.0, seconds) / WIND_KNOT_SECONDS
        index = int(pos)
        mix = pos - index
        mix = mix * mix * (3.0 - 2.0 * mix)
        left, right = self._sample(index), self._sample(index + 1)
        return self.base + WIND_GUST_LIMIT * (left + (right - left) * mix)


class Runner:
    """一个独立物理世界中的 11 刚体小人：4 个腿关节 + 2 个肩关节（肘部固定）+ 2 个随膝联动的踝关节（不可控制）。"""

    JOINT_NAMES = ("hip_L", "knee_L", "hip_R", "knee_R", "shoulder_L", "shoulder_R")

    def __init__(self, mode="normal", track=DEFAULT_TRACK, start=None, start_x=0.0, wind_seed=None, wind_strength=1.0):
        gravity, ground_friction = MODES[mode]
        self.mode = mode
        self.track = track
        self.space = pymunk.Space()
        self.space.gravity = (0, gravity)
        self.space.iterations = ITERATIONS
        self.time = 0.0
        self.frame = 0
        if not math.isfinite(wind_strength) or wind_strength < 0:
            raise ValueError('wind_strength 必须是有限非负数')
        self.wind_profile = WindProfile(wind_seed) if track == 'jump' and wind_seed is not None else None
        self.wind_strength = wind_strength
        body = TRACKS[track].get("body", {})          # 跳远的身体改动（见 JUMP_BODIES）；其它赛道为空 → 第 1 期常量
        self.ankle_limits = (body.get("ankle_lo", ANKLE_LIMITS[0]), body.get("ankle_hi", ANKLE_LIMITS[1]))
        self.hip_limits = body.get("hip_limits", HIP_LIMITS)          # 髋行程；没覆盖就用第 1 期常量
        self.knee_limits = body.get("knee_limits", KNEE_LIMITS)       # 膝行程（人体范围可覆盖）
        self.shoulder_limits = body.get("shoulder_limits", SHOULDER_LIMITS)   # 肩行程（人体范围可覆盖）
        self.ankle_ctrl = body.get("ankle_ctrl", False)               # True = 踝关节可单独控制（bot 返回 8 个数）；否则随膝联动
        self.ankle_cmd = None
        self.ankle_torque = body.get("ankle_torque", ANKLE_TORQUE)
        self.max_torque = body.get("max_torque", MAX_TORQUE)          # 髋/膝舵机力矩；没覆盖就用第 1 期常量
        self.shoulder_torque = body.get("shoulder_torque", SHOULDER_TORQUE)
        self.foot_l = body.get("foot_l", FOOT_L)                      # 脚长 (px)
        self.heel_shift = body.get("heel_shift", FOOT_HEEL_SHIFT)     # 脚掌中心相对踝点前移 (px)
        self.foot_m = body.get("foot_m", FOOT_M)
        self.torso_cog = body.get("torso_cog", 0.0)                   # 躯干质心沿躯干轴的偏移 (px，负 = 往髋部移)；外形不变
        self.space.damping = body.get("space_damping", 1.0)           # 每秒保留的速度比例（1.0 = 不衰减）；空气/整体能量衰减
        self.joint_damping = body.get("joint_damping", 0.0)           # 关节粘性阻尼：舵机目标角速度里减去 k·相对角速度
        self.angular_damping = body.get("angular_damping", 1.0)       # 每秒保留的角速度比例（只压旋转，不动平动）
        self.max_rate = body.get("max_rate", MAX_RATE)                # 舵机最大角速度（可单独降速，动作更钝）
        if "gravity" in body:                                          # 重力覆盖 (m/s²，正数)；没覆盖就用 MODES 的 9.0
            self.space.gravity = (0, -body["gravity"] * PPM)
        # 力–速度关系（仿肌肉，2026-11-04）：关节朝发力方向转得越快，可用力矩越小；
        # 转速达到 fv_w0 时只剩 fv_min 倍（1.0 = 关闭）。被反向拉伸（离心）时仍给满力矩。
        self.fv_min = body.get("fv_min", 1.0)
        self.fv_w0 = body.get("fv_w0", MAX_RATE)
        self.ecc = body.get("ecc", 1.0)          # 离心增强：关节被外力反向掰动时力矩 ×ecc（真人肌肉制动比发力强 1.3~1.5 倍）
        # 触沙放松（2026-11-04）：身体任何部位第一次碰到沙坑后，所有关节力矩降到 sand_torque 倍，
        # 落地后不能再从沙里蹬起来（真人落沙也蹬不动）。成绩照常从第一次落地量。
        self.sand_torque = body.get("sand_torque", TRACKS[track].get("sand_torque")) if track == "jump" else None
        self.in_sand = False

        self._build_ground(ground_friction)
        self._build_body()
        dx = start_x * PPM
        if track == 'jump' and TRACKS[track].get('free_takeoff'):
            hi = TRACKS[track].get('max_start')
            hi = TRACKS[track]['line'] if hi is None else hi
            if not math.isfinite(start_x) or not 2 <= start_x <= hi:
                raise ValueError(f'实验 START_X必须在2～{hi:g}之间')
            # 起点以脚尖而非躯干中心对齐，START_X=12时初始脚掌不会伸过踏板。
            dx -= max(shape.cache_bb().right for shape in self.feet)
        if dx:
            for b in self._w_damped:
                b.position = (b.position.x + dx, b.position.y)
            for b in self._w_damped:
                self.space.reindex_shapes_for_body(b)
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
        # [基线检查修复] 竖直挡墙（后墙/沙坑末端墙）不是"可踩的地面"。
        # 它们仍然参与碰撞和 ground_contacts() 计分，但从 contacts() 触地传感器里剔除，
        # 避免"靠在后墙上"被 is_down 判成"躯干/头触地"。
        self.wall_shapes = []
        pit_x0 = TRACKS[self.track]["pit"][0] if "pit" in TRACKS[self.track] else None
        for (x0, y0), (x1, y1) in zip(TRACKS[self.track]["terrain"], TRACKS[self.track]["terrain"][1:]):
            if pit_x0 is not None and x0 >= pit_x0:
                continue            # 沙坑段单独建（高摩擦）
            seg = pymunk.Segment(static, (x0 * PPM, y0 * PPM), (x1 * PPM, y1 * PPM), 2.0)
            self.ground_shapes.append(seg)
        # 起点后方的挡墙，防止倒车选手掉出世界（跳远 v4 可以改到出发位置之后）
        back = TRACKS[self.track].get("back_wall", -3.0)
        back_wall = pymunk.Segment(static, (back * PPM, 0), (back * PPM, 4.0 * PPM), 4.0)
        self.ground_shapes.append(back_wall)
        self.wall_shapes.append(back_wall)
        for x, h, w in TRACKS[self.track]["bumps"]:
            self.ground_shapes.append(_box(static, w * PPM, h * PPM, (x * PPM, h * PPM / 2)))
        for s in self.ground_shapes:
            s.friction = friction
        self.pit_shape = None
        t = TRACKS[self.track]
        if "pit" in t:              # 跳远：沙坑是一段独立的高摩擦地面（覆盖在起跳线之后）
            x0, x1, sand = t["pit"]
            pit = pymunk.Segment(static, (x0 * PPM, 0), (x1 * PPM, 0), 2.0)
            pit.friction = sand * friction
            self.ground_shapes.append(pit)
            self.pit_shape = pit
        if "end_wall" in t:
            wall = pymunk.Segment(static, (t["end_wall"] * PPM, 0), (t["end_wall"] * PPM, 4.0 * PPM), 4.0)
            wall.friction = friction
            self.ground_shapes.append(wall)
            self.wall_shapes.append(wall)
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
        if self.torso_cog:
            self.torso.center_of_gravity = (0, self.torso_cog)
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
            foot_b = _limb(self.foot_m, self.foot_l, FOOT_H, (self.heel_shift, ank_y - FOOT_H / 2 + 1))
            foot_s = _box(foot_b, self.foot_l, FOOT_H)
            foot_s.friction = FOOT_FRICTION
            self.space.add(thigh, shin, thigh_s, shin_s, foot_b, foot_s)
            ankle_m = pymunk.SimpleMotor(shin, foot_b, 0.0)
            ankle_m.max_force = self.ankle_torque
            self.space.add(pymunk.PivotJoint(shin, foot_b, (0, ank_y)),
                           pymunk.RotaryLimitJoint(shin, foot_b, *self.ankle_limits), ankle_m)
            self.foot_bodies.append(foot_b)
            self.ankle_motors.append((ankle_m, thigh, shin, foot_b))

            hip = pymunk.PivotJoint(self.torso, thigh, (0, hip_y))
            knee = pymunk.PivotJoint(thigh, shin, (0, hip_y - THIGH_H))
            hip_lim = pymunk.RotaryLimitJoint(self.torso, thigh, *self.hip_limits)
            knee_lim = pymunk.RotaryLimitJoint(thigh, shin, *self.knee_limits)
            hip_m = pymunk.SimpleMotor(self.torso, thigh, 0.0)
            knee_m = pymunk.SimpleMotor(thigh, shin, 0.0)
            for m in (hip_m, knee_m):
                m.max_force = self.max_torque
            self.space.add(hip, knee, hip_lim, knee_lim, hip_m, knee_m)

            self.thighs.append(thigh)
            self.shins.append(shin)
            self.feet.append(foot_s)
            self.motors += [(hip_m, self.torso, thigh, self.hip_limits), (knee_m, thigh, shin, self.knee_limits)]
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
            shoulder_lim = pymunk.RotaryLimitJoint(self.torso, arm, *self.shoulder_limits)
            elbow_lock = pymunk.RotaryLimitJoint(arm, fore, ELBOW_BEND, ELBOW_BEND)
            shoulder_m = pymunk.SimpleMotor(self.torso, arm, 0.0)
            shoulder_m.max_force = self.shoulder_torque
            self.space.add(shoulder, elbow, shoulder_lim, elbow_lock, shoulder_m)

            self.arms.append(arm)
            self.forearms.append(fore)
            self.motors.append((shoulder_m, self.torso, arm, self.shoulder_limits))

        # 每个形状的部位名（跳远计分/报告用）
        self.shape_names = {torso_shape: "躯干", head: "头"}
        for side, nm in ((0, "左"), (1, "右")):
            for s in self.thighs[side].shapes:
                self.shape_names[s] = nm + "大腿"
            for s in self.shins[side].shapes:
                self.shape_names[s] = nm + "小腿"
            for s in self.foot_bodies[side].shapes:
                self.shape_names[s] = nm + "脚"
            for s in self.arms[side].shapes:
                self.shape_names[s] = nm + "上臂"
            for s in self.forearms[side].shapes:
                self.shape_names[s] = nm + "前臂/手"
        self.body_shapes = list(self.shape_names)
        self._w_damped = [self.torso, *self.thighs, *self.shins, *self.foot_bodies, *self.arms, *self.forearms]
        self._fv_motors = [(m, a, b, m.max_force) for m, a, b, _lim in self.motors] +                           [(m, sh, ft, m.max_force) for m, _th, sh, ft in self.ankle_motors]

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
        if self.ankle_ctrl:          # 踝可控时 joints 末尾追加 [ankle_L, ankle_R]
            lo, hi = self.ankle_limits
            for _m, shin, foot in ((m, sh, ft) for m, _th, sh, ft in self.ankle_motors):
                out.append(max(-1.0, min(1.0, 2.0 * ((foot.angle - shin.angle) - lo) / (hi - lo) - 1.0)))
        return out

    def _touching(self, body, shapes):
        hit = [False]
        # [基线检查修复] 触地传感器只认地面/障碍，不认竖直挡墙。
        ground = (set(self.ground_shapes) - set(getattr(self, "wall_shapes", ()))) \
            | {s for h in self.hurdles for s in h.shapes}

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
            "wind": self.wind_at(self.time),
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
            "dist_to_line": (TRACKS[self.track]["line"] - x_m) if "line" in TRACKS[self.track] else 99.0,
        }

    def ground_contacts(self):
        """[(形状, 接触点 x (m))]：本小人所有与地面（含墙）接触的形状及每个接触点的水平位置。"""
        ground = set(self.ground_shapes)
        out = []
        for body in (self.torso, *self.thighs, *self.shins, *self.foot_bodies, *self.arms, *self.forearms):
            def check(arb, out=out):
                a, b = arb.shapes
                if b in ground and a in self.shape_names:
                    me = a
                elif a in ground and b in self.shape_names:
                    me = b
                else:
                    return
                for p in arb.contact_point_set.points:
                    out.append((me, (p.point_a.x + p.point_b.x) / 2 / PPM))
            body.each_arbiter(check)
        return out

    def min_x(self):
        """全身所有形状最靠后（最小 x）的位置 (m) 及部位名。"""
        best = min(self.body_shapes, key=lambda s: s.bb.left)
        return best.bb.left / PPM, self.shape_names[best]

    # -- 控制与推进 --------------------------------------------------------
    def apply(self, action):
        self.ankle_cmd = None
        if action is None or len(action) not in (4, N_ACTIONS, N_ACTIONS + 2):
            action = REST_ACTION
        elif len(action) == 4:      # 兼容仅控制腿的旧 bot，手臂保持自然下垂
            action = list(action) + REST_ACTION[4:]
        elif len(action) == N_ACTIONS + 2:
            if self.ankle_ctrl:     # 8 个数：最后两个是 [ankle_L, ankle_R]
                self.ankle_cmd = []
                for u in action[N_ACTIONS:]:
                    try:
                        u = float(u)
                    except (TypeError, ValueError):
                        u = 0.0
                    self.ankle_cmd.append(max(-1.0, min(1.0, u)) if math.isfinite(u) else 0.0)
            action = list(action)[:N_ACTIONS]
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
            rate = max(-self.max_rate, min(self.max_rate, KP * err))
            if self.joint_damping:
                rate -= self.joint_damping * (b.angular_velocity - a.angular_velocity)
            motor.rate = -rate      # pymunk: b.w - a.w = -rate

    def _drive_ankles(self):
        """踝关节随膝联动（每个物理子步更新一次目标）。"""
        for i, (motor, thigh, shin, foot) in enumerate(self.ankle_motors):
            if self.ankle_cmd is not None:      # 踝单独控制：-1 = 脚尖向下蹬直（ankle_lo），+1 = 脚尖上翘（ankle_hi）
                lo, hi = self.ankle_limits
                target = lo + (self.ankle_cmd[i] + 1.0) / 2.0 * (hi - lo)
            else:
                flex = max(0.0, -(shin.angle - thigh.angle) - math.radians(5))
                target = min(self.ankle_limits[1], ANKLE_COUPLE * flex)
            err = target - (foot.angle - shin.angle)
            rate = max(-self.max_rate, min(self.max_rate, KP * 2 * err))
            if self.joint_damping:
                rate -= self.joint_damping * (foot.angular_velocity - shin.angular_velocity)
            motor.rate = -rate

    def _apply_fv(self):
        """力–速度：同向（向心）转速越快，最大力矩线性降到 fv_min 倍；反向（离心）给满力矩。"""
        for m, a, b, base in self._fv_motors:
            w = b.angular_velocity - a.angular_velocity        # pymunk 电机让 b.w - a.w 趋向 -rate
            if w * -m.rate > 0:
                m.max_force = base * (1.0 - (1.0 - self.fv_min) * min(1.0, abs(w) / self.fv_w0))
            elif w * -m.rate < 0:
                m.max_force = base * self.ecc
            else:
                m.max_force = base

    def wind_at(self, seconds):
        return self.wind_profile.at(seconds) if self.wind_profile is not None and self.wind_strength else 0.0

    def step(self, action):
        self.apply(action)
        sub = DT / SUBSTEPS
        collect = SUBSTEP_CONTACTS and "line" in TRACKS[self.track]
        self.step_contacts = []
        wf = self.angular_damping ** sub if self.angular_damping != 1.0 else None
        for substep in range(SUBSTEPS):
            wind = self.wind_at(self.time + substep * sub)
            if wind:
                force = self.torso.mass * PPM * WIND_ACCEL_PER_MPS * self.wind_strength * wind
                center = self.torso.local_to_world(self.torso.center_of_gravity)
                self.torso.apply_force_at_world_point((force, 0.0), center)
            self._drive_ankles()
            if (self.fv_min < 1.0 or self.ecc != 1.0) and not self.in_sand:
                self._apply_fv()
            self.space.step(sub)
            if wf is not None:          # 只衰减角速度：压住翻滚/转体，不动平动
                for b in self._w_damped:
                    b.angular_velocity *= wf
            if collect:                 # 跳远判定用：本帧所有物理子步的地面接触点（只看最后一个子步会漏掉短于 1/60s 的触地）
                c = self.ground_contacts()
                self.step_contacts += c
                if self.sand_torque is not None and not self.in_sand and                         any(x >= TRACKS["jump"]["pit"][0] - 1e-9 for _s, x in c):
                    self.in_sand = True
                    for m, _a, _b, base in self._fv_motors:
                        m.max_force = base * self.sand_torque
        self.time += DT
        self.frame += 1

    # -- 渲染用 ------------------------------------------------------------
    def parts(self):
        """[(body, shape)] 供可视化器绘制。"""
        return [(s.body, s) for s in self.space.shapes if s.body is not self.space.static_body]
