import math

START_X = 7.0

# ---------------- 助跑（approach 包参数，逐字复制） ----------------
STRIDE_PERIOD = 0.9427686943729606
HIP_SWING = 0.4612432499188154
KNEE_BASE = 0.0
KNEE_SWING = 0.46037503371745675
HIP_OFFSET = 0.16081091205893727
LEG_OFFSET = -0.11176635736923067
KNEE_PHASE = -0.6848588702859837
BALANCE_GAIN = -0.8549383067209367
BALANCE_DAMPING = -0.019411040799691966

# ---------------- 起跳/落地参数（自测调定，见 log.md） ----------------
T_TRIG = 11.35      # 躯干 x 达到此值进入起跳准备（留约 0.6m 起跳远度余量）
PUSH_MAX = 0.30     # 蹬伸最长时间 s
AIR_GRACE = 2       # 连续无接触帧数确认腾空
WEAK_VY = 2.7       # 腾空前 0.18s 峰值竖直速度低于此值 → 判为弱跳，切换救援落地

_st = {'phase': 'run', 't0': 0.0, 'side': 0, 'noc': 0,
       'air_t0': 0.0, 'peak_vy': 0.0, 'weak': False, 'decided': False}


def gait(obs):
    phase = obs['time'] * math.tau / STRIDE_PERIOD
    ramp = min(1, obs['time'] / 0.5)
    legs, ankles = [], []
    for side in (0, 1):
        leg_phase = phase + side * math.pi
        swing = ramp * HIP_SWING * math.cos(leg_phase) + LEG_OFFSET
        knee = KNEE_BASE - ramp * KNEE_SWING * max(0, -math.sin(leg_phase + KNEE_PHASE))
        hip = swing + HIP_OFFSET + BALANCE_GAIN * obs['torso_angle'] + BALANCE_DAMPING * obs['torso_omega']
        ankle = -swing - knee
        legs.extend([(hip * 180 / math.pi - 7.5) / 67.5, 1 + knee * 180 / math.pi / 55])
        ankles.append((ankle * 180 / math.pi - 5) / 55)
    return legs + [-0.39, -0.39] + ankles


def act(obs):
    c = obs['contact']
    lc, rc = c['foot_L'], c['foot_R']
    ph = _st['phase']

    if ph == 'run':
        if obs['x'] >= T_TRIG:
            _st['phase'] = 'plant'
            _st['t0'] = obs['time']
        else:
            return gait(obs)

    if ph == 'plant':
        # 空中则收腿等待下一次脚着地作为起跳脚；已着地立刻蹬伸
        if not lc and not rc:
            return [0.35, -0.35, 0.35, -0.35, 0.0, 0.0, 0.5, 0.5]
        _st['side'] = 0 if lc else 1
        _st['phase'] = 'push'
        _st['t0'] = obs['time']
        _st['noc'] = 0

    if ph == 'push':
        side = _st['side']
        # 起跳腿：伸髋伸膝踝蹬地；摆动腿：屈髋提膝；双臂前上摆
        a = [0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0]
        if side == 0:
            a[0], a[1], a[6] = -0.25, 1.0, -1.0   # 左蹬
            a[2], a[3], a[7] = 1.0, -0.6, 0.2      # 右摆
        else:
            a[2], a[3], a[7] = -0.25, 1.0, -1.0
            a[0], a[1], a[6] = 1.0, -0.6, 0.2
        _st['noc'] = _st['noc'] + 1 if not lc and not rc else 0
        if _st['noc'] >= AIR_GRACE or obs['time'] - _st['t0'] > PUSH_MAX:
            _st['phase'] = 'air'
            _st['air_t0'] = obs['time']
            _st['peak_vy'] = obs['vy']
            _st['weak'] = False
            _st['decided'] = False
        return a

    if ph == 'air':
        dt = obs['time'] - _st['air_t0']
        if not _st['decided']:
            _st['peak_vy'] = max(_st['peak_vy'], obs['vy'])
            if dt >= 0.18:
                _st['decided'] = True
                _st['weak'] = _st['peak_vy'] < WEAK_VY
        if lc or rc:
            _st['phase'] = 'land'
        elif _st['weak']:
            # 弱跳救援：团身段双臂全力后摆补前向角动量，下降段提前前伸双腿勾脚
            if obs['vy'] <= -0.3:
                return [0.55, 0.25, 0.55, 0.25, -1.0, -1.0, 1.0, 1.0]
            return [0.85, -0.7, 0.85, -0.7, -1.0, -1.0, 0.3, 0.3]
        else:
            # 强跳：收团 → 下降前伸脚跟触沙
            if obs['vy'] <= -0.6:
                return [0.65, 0.15, 0.65, 0.15, 1.0, 1.0, 0.9, 0.9]
            return [1.0, -0.9, 1.0, -0.9, 1.0, 1.0, 0.3, 0.3]

    # 落地
    if _st.get('weak'):
        # 强屈髋把身体拉过落点，借前向角动量完成前扑，避免后坐头先着地
        return [1.0, -0.55, 1.0, -0.55, -0.2, -0.2, 1.0, 1.0]
    # 强跳：屈髋前带、双臂保持在前，防止手/臀在脚跟印记后方触地
    return [0.75, -0.15, 0.75, -0.15, 1.0, 1.0, 0.8, 0.8]
