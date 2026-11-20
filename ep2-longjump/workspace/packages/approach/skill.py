"""trial_old60_k0 低速交替小跑；无起跳、收腿或落地控制。

从前脚尖7m启动。到板前仍持续迈步，选手应自行决定接管时机；不会自动停稳。
参数仅在该实验身体验证，不适用于其他身体。改变步频不保证速度单调变化。
"""
import math

START_X = 7.0
STRIDE_PERIOD = 0.9427686943729606
HIP_SWING = 0.4612432499188154
KNEE_BASE = 0.0
KNEE_SWING = 0.46037503371745675
HIP_OFFSET = 0.16081091205893727
LEG_OFFSET = -0.11176635736923067
KNEE_PHASE = -0.6848588702859837
BALANCE_GAIN = -0.8549383067209367
BALANCE_DAMPING = -0.019411040799691966


def gait_state(obs):
    """相位是指令相位；实际支撑状态来自接触观测，不用相位猜测。"""
    return {
        'phase': (obs['time'] * math.tau / STRIDE_PERIOD) % math.tau,
        'left_contact': obs['contact']['foot_L'],
        'right_contact': obs['contact']['foot_R'],
        'torso_distance_to_board': 12.0 - obs['x'],
        'vx': obs['vx'],
    }


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
    return gait(obs)
