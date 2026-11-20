import math

# 模块级状态（每次评测重新加载）
_state = {
    "leg": 0,           # 当前摆动腿: 0=左腿摆动/右腿支撑, 1=右腿摆动/左腿支撑
    "step_t": 0.0,      # 当前步已持续时间
    "last_t": 0.0,
    "stance_hip": -0.11,
    "recover_mode": 0,  # 0=正常, 1=前趴跪立起身, 2=后仰起身
    "recover_t": 0.0,
}


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def act(obs: dict) -> list[float]:
    t = obs["time"]
    x = obs["x"]
    y = obs["y"]
    vx = obs["vx"]
    vy = obs["vy"]
    ang = obs["torso_angle"]       # >0 前倾, <0 后仰
    omega = obs["torso_omega"]     # >0 正在前倾
    joints = obs["joints"]         # [hip_L, knee_L, hip_R, knee_R, sh_L, sh_R]
    c = obs["contact"]
    bump_dist = obs["next_bump_dist"]
    bump_h = obs["next_bump_height"]

    dt = t - _state["last_t"] if t > _state["last_t"] else (1.0 / 60.0)
    if dt <= 0.0 or dt > 0.1:
        dt = 1.0 / 60.0
    _state["last_t"] = t
    _state["step_t"] += dt

    # 0. 起步前倾引导（前 0.22 秒）：利用左膝微屈触发踝关节背屈前拉重心，右腿屈膝前摆建立前倾
    if t < 0.22 and x < 0.3:
        _state["leg"] = 1  # 右腿先摆动，左腿支撑
        _state["step_t"] = t
        _state["stance_hip"] = joints[0]
        return [0.05, 0.55, 0.55, -0.75, 0.85, 0.85]

    # 1. 冲过 18m 终点后平稳减速停住
    if x >= 18.05:
        if abs(vx) < 0.15:
            corr = _clamp(0.8 * ang + 0.2 * omega, -0.35, 0.35)
            return [_clamp(-0.08 + corr), 0.78, _clamp(-0.08 - corr), 0.78, 0.1, 0.1]
        return [0.28, 0.65, -0.22, 0.75, -0.3, -0.3]

    # 2. 倒地检测与三段式“跪立->弓步站起”恢复逻辑
    is_fallen = c["torso"] or y < 0.45 or (abs(ang) > 0.87 and y < 0.70)
    if _state["recover_mode"] == 0 and is_fallen and t > 1.5:
        _state["recover_mode"] = 1 if ang >= 0.0 else 2
        _state["recover_t"] = 0.0

    if _state["recover_mode"] != 0:
        _state["recover_t"] += dt
        rt = _state["recover_t"]
        if y > 0.90 and abs(ang) < 0.45 and (c["foot_L"] or c["foot_R"]):
            _state["recover_mode"] = 0
            _state["step_t"] = 0.0
            _state["leg"] = 0 if joints[0] < joints[2] else 1
            _state["stance_hip"] = joints[2 if _state["leg"] == 0 else 0]
        elif rt > 1.8:
            _state["recover_mode"] = 0
            _state["step_t"] = 0.0
            _state["stance_hip"] = joints[2 if _state["leg"] == 0 else 0]
        elif _state["recover_mode"] == 1:
            # 面朝下前趴起身：
            # 阶段1 (0~0.35s)：完全折叠双膝(-1.0)，将双大腿前收到胸下(+1.0)
            # 阶段2 (0.35~0.85s)：保持双膝完全折叠(-1.0)以膝盖/小腿为支点，双髋向后拉至 -0.15 把躯干竖直立起成“跪姿”
            # 阶段3 (0.85~1.35s)：左腿前迈成弓步，右腿伸膝蹬起
            # 阶段4 (1.35~1.80s)：双腿伸直将躯干顶过 0.70m 计分线并向前推进
            if rt < 0.35:
                return [1.0, -1.0, 1.0, -1.0, 1.0, 1.0]
            elif rt < 0.85:
                return [-0.15, -1.0, -0.15, -1.0, -0.35, -0.35]
            elif rt < 1.35:
                return [0.55, 0.65, -0.20, 0.90, 0.50, 0.50]
            else:
                return [0.15, 0.90, -0.10, 0.92, 0.60, 0.60]
        else:
            # 仰躺起身
            if rt < 0.35:
                return [-1.0, -1.0, -1.0, -1.0, -1.0, -1.0]
            elif rt < 0.85:
                return [0.15, -1.0, 0.15, -1.0, 1.0, 1.0]
            elif rt < 1.35:
                return [0.45, 0.75, -0.15, 0.90, 0.80, 0.80]
            else:
                return [0.15, 0.90, -0.10, 0.92, 0.70, 0.70]

    # 3. 赛段自适应参数
    target_lean = 0.20
    vx_des = 1.35
    step_dur = 0.28
    lift_bonus = 0.0

    if 4.8 <= x < 9.0:
        # 减速带区域
        target_lean = 0.22
        vx_des = 1.30
        lift_bonus = 0.18
    elif 9.0 <= x < 11.0:
        # 15° 上坡
        target_lean = 0.32
        vx_des = 1.20
        step_dur = 0.25
        lift_bonus = 0.22
    elif 11.0 <= x < 13.0:
        # 15° 下坡
        target_lean = 0.08
        vx_des = 1.30
        step_dur = 0.30
        lift_bonus = 0.05
    elif x >= 13.0:
        # 跨栏区域
        target_lean = 0.22
        vx_des = 1.35
        lift_bonus = 0.28

    if 0.05 < bump_dist < 0.80:
        if bump_h >= 0.15:
            lift_bonus = max(lift_bonus, 0.40)
        else:
            lift_bonus = max(lift_bonus, 0.22)

    # 4. 步态切换判定（SIMBICON 触地/周期状态机）
    swing_leg = _state["leg"]
    stance_leg = 1 - swing_leg
    swing_foot_touch = c["foot_L"] if swing_leg == 0 else c["foot_R"]

    if (_state["step_t"] >= 0.16 and swing_foot_touch) or (_state["step_t"] >= step_dur):
        _state["leg"] = stance_leg
        _state["step_t"] = 0.0
        swing_leg = _state["leg"]
        stance_leg = 1 - swing_leg
        _state["stance_hip"] = joints[0 if stance_leg == 0 else 2]

    st = _state["step_t"]
    prog = min(1.0, st / step_dur)

    # 5. 摆动腿控制（世界系基准 u_vert + Capture Point 落脚点调节）
    u_vert = -0.11 + 0.85 * ang
    cp_offset = 0.22 * (vx - vx_des) + 0.95 * (ang - target_lean) + 0.22 * omega

    if prog < 0.55:
        swing_hip = u_vert + 0.42 + 0.25 * lift_bonus + 0.7 * cp_offset
        swing_knee = -0.70 - 0.30 * lift_bonus
    else:
        ext = (prog - 0.55) / 0.45
        swing_hip = u_vert + 0.32 + 0.15 * lift_bonus + cp_offset
        swing_knee = -0.20 + 1.02 * ext
        if ang > 0.26:
            swing_knee = max(swing_knee, 0.15 + 0.82 * ext)

    # 6. 支撑腿控制（大前倾 >0.25 时刚性直膝 0.96 顶住 y>=0.72m 并加强逆时针抬头力矩）
    lean_state = ang + 0.15 * omega
    if lean_state > 0.08:
        push_rate = 1.65 + 1.5 * (lean_state - 0.08)
        stance_knee = 0.96 if lean_state > 0.25 else 0.88
    else:
        push_rate = 0.20
        stance_knee = 0.55

    _state["stance_hip"] = _clamp(_state["stance_hip"] - push_rate * dt, -0.45, 0.45)
    stance_hip = _state["stance_hip"]

    if lean_state > 0.25:
        stance_hip -= 0.85 * (lean_state - 0.25)

    if swing_leg == 0:
        hip_L, knee_L = _clamp(swing_hip), _clamp(swing_knee)
        hip_R, knee_R = _clamp(stance_hip), _clamp(stance_knee)
    else:
        hip_L, knee_L = _clamp(stance_hip), _clamp(stance_knee)
        hip_R, knee_R = _clamp(swing_hip), _clamp(swing_knee)

    # 7. 双臂控制
    sh_base = _clamp(0.55 - 1.2 * (ang - target_lean) - 0.20 * omega)
    sh_diff = 0.20 if swing_leg == 0 else -0.20
    sh_L = _clamp(sh_base - sh_diff)
    sh_R = _clamp(sh_base + sh_diff)

    return [hip_L, knee_L, hip_R, knee_R, sh_L, sh_R]