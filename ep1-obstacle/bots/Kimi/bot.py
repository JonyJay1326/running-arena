import math


recovering = False
recoveries = 0
down_since = None
getting_up = False


def clamp(x):
    return max(-1.0, min(1.0, x))


def act(obs: dict) -> list[float]:
    global recovering, recoveries, down_since, getting_up
    t = obs['time']
    angle = obs['torso_angle']
    omega = obs['torso_omega']
    if obs['x'] >= 18.05:
        return [-1.0, 1.0, -1.0, 1.0, -1.0, -1.0]
    if obs['contact']['torso'] or obs['y'] < 0.45:
        getting_up = True
        if down_since is None:
            down_since = t
    wrapped = math.atan2(math.sin(angle), math.cos(angle))
    if getting_up and obs['y'] > 1.05 and abs(wrapped) < 0.6:
        getting_up = False
        down_since = None
        recovering = False
    if getting_up:
        period = 1.2 if obs['x'] < 13.0 else 0.95
        if (t - down_since) % period < period / 2.0:
            return [1.0, -1.0, 1.0, -1.0, 1.0, 1.0]
        return [-1.0, 1.0, -1.0, 1.0, -1.0, -1.0]
    if angle > 0.42 and not recovering:
        recovering = True
        recoveries += 1
    elif recovering and angle < 0.20 and omega < 0.0:
        recovering = False
    if recovering:
        return [-0.65, 0.85, -0.65, 0.85, -1.0, -1.0]
    balance = 0.55 - angle - 0.20 * omega
    left_swing = math.sin(2.0 * math.pi * t) >= 0.0
    if left_swing:
        hip_l = balance + 0.35
        knee_l = -0.10
        hip_r = balance - 0.35
        knee_r = 0.85
    else:
        hip_l = balance - 0.35
        knee_l = 0.85
        hip_r = balance + 0.35
        knee_r = -0.10
    return [clamp(hip_l), knee_l, clamp(hip_r), knee_r, -0.39, -0.39]
