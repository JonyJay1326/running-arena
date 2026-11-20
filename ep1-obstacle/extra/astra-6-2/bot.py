import math


stance = 1
step_start = 0.0
finish_time = None
step_speed = 0.0
step_pitch = 0.0


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def brake(obs, elapsed):
    if elapsed >= 0.65:
        return [0.73, -0.84, 0.73, -0.84, -0.64, -0.64]
    pitch = obs["torso_angle"]
    hip_y = obs["y"] - 0.30 * math.cos(pitch)
    angle = clamp(math.acos(clamp((hip_y - 0.07) / 0.90, 0, 1)), 0.20, 0.75)
    hip = clamp((pitch + angle - 0.085) / 1.18, -1, 1)
    return [hip, 1, hip, 1, -0.90, -0.90]


def grade_ahead(x, vx):
    look = x + 0.16 * max(0, vx)
    ascent = clamp((look - 8.4) / 0.9, 0, 1)
    crest = clamp((look - 10.5) / 0.85, 0, 1)
    descent_end = clamp((look - 12.6) / 0.8, 0, 1)
    return ascent - 2 * crest + descent_end


def act(obs):
    global stance, step_start, step_speed, step_pitch, finish_time
    t, x, vx = obs["time"], obs["x"], obs["vx"]
    if t == 0:
        stance, step_start, step_speed, step_pitch = 1, 0.0, 0.0, 0.0
        finish_time = None
    if x >= 18 and finish_time is None:
        finish_time = t
    if finish_time is not None:
        return brake(obs, t - finish_time)

    pitch, omega = obs["torso_angle"], obs["torso_omega"]
    feet = [obs["contact"]["foot_L"], obs["contact"]["foot_R"]]
    q = obs["joints"]
    age = t - step_start
    cycle = clamp(0.34 - 0.035 * step_speed, 0.19, 0.36)
    landed = feet[1 - stance] and age > cycle * (0.78 if feet[stance] else 0.55)
    stalled = age > 0.55 and vx < 0.8
    if landed or stalled:
        stance = 1 - stance
        step_start = t
        step_speed = vx
        step_pitch = pitch
        age = 0
    phase = min(1, age / cycle)
    support, swing = 2 * stance, 2 * (1 - stance)
    legs = [0.0] * 4
    if t < 0.30:
        load = min(1, t / 0.13)
        legs[support] = q[support] + 1.1 * (0.64 - pitch) - 0.18 * omega
        legs[support + 1] = 0.92 - 0.95 * math.sin(math.pi * t / 0.30)
        legs[swing] = 0.36 + 0.42 * load
        legs[swing + 1] = 0.9 - 1.2 * load
        return [clamp(value, -1, 1) for value in legs + [0.8, 0.8]]

    grade = grade_ahead(x, vx)
    target = 0.24 - 0.10 * abs(grade) - 0.13 * clamp(vx - 2.0, 0, 1.5)
    legs[support] = q[support] - 0.13 + 1.15 * (target - pitch) - 0.19 * omega
    shallow = clamp((step_speed - 1.9) / 0.95, 0, 1)
    knee_load = -0.02 + 0.17 * shallow
    push = clamp((phase - 0.14 * (1 - shallow)) / 0.44, 0, 1)
    ankle_x = 0.0
    if step_speed > 1.35:
        thigh = math.radians(7.5 + 67.5 * q[support]) - pitch
        shin = thigh - math.radians(57.5 - 52.5 * q[support + 1])
        ankle_x = 0.45 * (math.sin(thigh) + math.sin(shin))
        push = clamp((0.22 - ankle_x) / 0.30, 0, 1)
    legs[support + 1] = knee_load + (1 - knee_load) * push

    # 由预测落脚点和膝屈曲角求髋角，使收膝与脚的水平位置解耦。
    fold = 1 - clamp((phase - 0.22) / 0.59, 0, 1)
    knee = (0.09 + 0.42 * shallow) * (1 - fold) - 0.96 * fold
    flexion = math.radians(57.5 - 52.5 * knee)
    travel_speed = 0.65 * step_speed + 0.35 * vx
    foot_x = clamp(
        0.16 * travel_speed - 0.15 + 0.07 * abs(grade) + 0.16 * (pitch - target),
        -0.30, 0.58,
    )
    if x > 11.0 and step_speed > 1.35:
        # 重心越过旧支点越远，下一步的接脚就要更靠前。
        foot_x += 0.48 * clamp(-ankle_x - 0.12, 0, 0.25) * clamp((step_speed - 1.8) / 0.85, 0, 1)
    if x > 7.1:
        upright = clamp((0.40 - step_pitch) * 3.0, 0, 1)
        foot_x -= 0.10 * upright * clamp(3.65 - step_speed, 0, 1) * clamp((12.0 - x) / 1.4, 0, 1)
    if 10.5 < x < 14.2:
        foot_x += 0.12 * clamp(-obs["vy"] - 0.25, 0, 1.25) * clamp(3 * (pitch + 0.09 * omega - 0.08), 0, 1)
    if x > 14.0 and step_speed < 2.6:
        foot_x -= clamp((2.6 - step_speed) * 0.14, 0, 0.14)
    if x > 14.6:
        backsweep = clamp((0.78 - phase) * cycle * step_speed, -0.14, 0.13)
        foot_x += backsweep * clamp((x - 14.6) / 0.8, 0, 1)
    hip_world = math.asin(clamp(foot_x / (0.90 * math.cos(flexion / 2)), -0.80, 0.80)) + flexion / 2
    swing_pitch = pitch
    if x > 7.1:
        swing_pitch += 0.70 * fold * (step_pitch - pitch)
    legs[swing] = (hip_world + swing_pitch - math.radians(7.5)) / math.radians(67.5)
    legs[swing + 1] = knee
    swing_error = clamp(legs[swing], -1, 1) - q[swing]
    recovery = clamp((x - 12.0) / 1.2, 0, 1) * clamp((16.25 - x) / 1.15, 0, 1)
    recovery *= clamp(0.45 + 0.55 * (step_speed - 2.2), 0.18, 1)
    legs[support] -= 0.36 * swing_error
    if recovery > 0:
        timed_knee = 0.40 + 0.60 * min(1, age / 0.145)
        legs[support + 1] += recovery * (timed_knee - legs[support + 1])
    arm = 0.15 - 0.30 * (pitch - target) - 0.08 * omega
    if x > 7.5:
        # 摆腿抬髋的同时双臂后摆，在支撑后半程将手臂送回前方。
        arm += clamp((x - 7.5) / 0.9, 0, 1) * (0.42 - 1.15 * max(0, swing_error))
    return [clamp(value, -1, 1) for value in legs + [arm, arm]]
