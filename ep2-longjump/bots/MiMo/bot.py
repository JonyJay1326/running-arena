import math

START_X = 9.0

STRIDE_PERIOD = 0.9427686943729606
HIP_SWING = 0.4612432499188154
KNEE_SWING = 0.46037503371745675
HIP_OFFSET = 0.30
LEG_OFFSET = -0.11176635736923067
KNEE_PHASE = -0.6848588702859837
BALANCE_GAIN = -0.8549383067209367
BALANCE_DAMPING = -0.019411040799691966


def gait(obs):
    phase = obs['time'] * math.tau / STRIDE_PERIOD
    ramp = min(1, obs['time'] / 0.5)
    legs, ankles = [], []
    for side in (0, 1):
        leg_phase = phase + side * math.pi
        swing = ramp * HIP_SWING * math.cos(leg_phase) + LEG_OFFSET
        knee = -ramp * KNEE_SWING * max(0, -math.sin(leg_phase + KNEE_PHASE))
        hip = swing + HIP_OFFSET + BALANCE_GAIN * obs['torso_angle'] + BALANCE_DAMPING * obs['torso_omega']
        ankle = -swing - knee
        legs.extend([(hip * 180 / math.pi - 7.5) / 67.5, 1 + knee * 180 / math.pi / 55])
        ankles.append((ankle * 180 / math.pi - 5) / 55)
    return legs + [-0.39, -0.39] + ankles


X_TRIG = 1.2
T_CROUCH = 0.30
T_PUSH = 0.16
NEED_STABLE = 4

POSE_CROUCH = [0.45, -0.35, 0.45, -0.35, -0.5, -0.5, 0.57, 0.57]
POSE_PUSH = [-0.30, 1.0, -0.30, 1.0, 0.6, 0.6, -1.0, -1.0]
POSE_FLIGHT = [0.95, 0.40, 0.95, 0.40, 0.0, 0.0, 0.80, 0.80]

STATE = 0
T_ENTER = 0.0
STABLE = 0


def act(obs):
    global STATE, T_ENTER, STABLE
    t = obs['time']
    c = obs['contact']
    both = c['foot_L'] and c['foot_R']
    grounded = c['foot_L'] or c['foot_R'] or c['hand_L'] or c['hand_R'] or c['torso']
    vy = obs['vy']

    if STATE == 0:
        if obs['dist_to_line'] < X_TRIG:
            STATE = 1
            T_ENTER = t
            STABLE = 0
        return gait(obs)

    if STATE == 1:
        if both and abs(vy) < 0.35:
            STABLE += 1
        else:
            STABLE = 0
        if STABLE >= NEED_STABLE or obs['dist_to_line'] < 0.4:
            STATE = 2
            T_ENTER = t
        return gait(obs)

    if STATE == 2:
        knee_u = obs['joints'][1]
        if t - T_ENTER >= T_CROUCH and knee_u < -0.10:
            STATE = 3
            T_ENTER = t
        elif t - T_ENTER >= T_CROUCH * 2.5:
            STATE = 3
            T_ENTER = t
        return POSE_CROUCH

    if STATE == 3:
        if not grounded:
            STATE = 4
        elif t - T_ENTER >= T_PUSH:
            STATE = 4
        return POSE_PUSH

    return POSE_FLIGHT
