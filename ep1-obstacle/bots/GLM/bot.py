import math

# ---------- v12 原配方（实测最好走姿）+ 倒地装死 ----------
HIP_BIAS = 0.15        # 恒定髋目标(前倾偏置)：身体绕脚尖前旋产生前进动力
KSTAND = 0.95          # "伸直"膝：绝不压死 -5° 限位（否则脚尖被往地里拧→身体被推后）
KTUCK = 0.40          # 双脚腾空且未快速下落时屈膝提脚尖
LEAN_T = 0.05         # 目标前倾 (rad)
KP, KD = 0.35, 0.05   # 躯干闭环增益（符号随触地状态翻转！）
ARM_TUCK, ARM_STAND = 0.35, -0.55   # 腾空时手臂前摆/支撑时手臂后摆
DOWN_ANG = math.radians(50)

_state = {"dead": False}


def _down(o):
    ang = (o["torso_angle"] + math.pi) % (2 * math.pi) - math.pi
    return o["contact"]["torso"] or o["y"] < 0.45 or (abs(ang) >= DOWN_ANG and o["y"] < 0.70)


def act(obs):
    t = obs["time"]
    x = obs["x"]
    ang = obs["torso_angle"]
    om = obs["torso_omega"]
    vy = obs["vy"]
    c = obs["contact"]

    # 冲线后：站住别动
    if x >= 18.1:
        return [-0.11, KSTAND, -0.11, KSTAND, -0.39, -0.39]

    # 倒地后：趴平装死（乱扑腾只会把最终位置弄差；得分≈摔过线时的位置）
    if _state["dead"]:
        return [0.35, -0.75, 0.35, -0.75, 0.85, 0.85]
    if _down(obs):
        _state["dead"] = True
        return [0.35, -0.75, 0.35, -0.75, 0.85, 0.85]

    # 起步偏置渐增 0.6s
    amp = min(1.0, t / 0.6)

    e = ang - LEAN_T
    if c["foot_L"] or c["foot_R"]:
        # 触地(pinned)：髋后压→躯干后仰可纠正前倾 → 负反馈取负号
        shift = max(-0.15, min(0.15, -(KP * e + KD * om)))
        knee = KSTAND
        sh = ARM_STAND
    else:
        # 腾空(free)：自由腿前摆→躯干后仰；此时前倾应让髋目标前送 → 取正号
        shift = max(-0.15, min(0.15, KP * e + KD * om))
        knee = KTUCK if vy > -0.5 else KSTAND
        sh = ARM_TUCK

    hip = -0.11 + (HIP_BIAS + 0.11) * amp + shift
    return [hip, knee, hip, knee, sh, sh]
