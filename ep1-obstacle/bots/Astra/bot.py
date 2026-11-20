import math

side = 0
changed = 0.0
finished = False
previous_x = 0.0
fallen_distance = 0.0


def act(obs: dict) -> list[float]:
    """触地换脚、足端逆运动学与分段姿态控制。"""
    global side, changed, finished, previous_x, fallen_distance
    angle = (obs['torso_angle'] + math.pi) % (2 * math.pi) - math.pi
    down = (
        obs['contact']['torso']
        or obs['y'] < 0.45
        or (abs(angle) >= math.radians(50) and obs['y'] < 0.7)
    )
    # 仅在有效计分进度越过终点后锁定停车。
    if down:
        fallen_distance += obs['x'] - previous_x
    previous_x = obs['x']
    if obs['x'] - max(0.0, fallen_distance) >= 18.15:
        finished = True
    if finished:
        return [1.0, -1.0, 1.0, -1.0, -0.4, -0.4]
    t = obs['time']
    a = obs['torso_angle']
    w = obs['torso_omega']
    dt = t - changed
    c = obs['contact']
    quick = 1.0 < obs['x'] < 8.8
    if (
        dt > (0.42 if quick else 0.5)
        or (
            dt > (0.20 if quick else 0.22)
            and c['foot_R' if side == 0 else 'foot_L']
        )
    ):
        side = 1 - side
        changed = t
        dt = 0.0
    uphill = 8.8 < obs['x'] < 11.0
    downhill = 10.5 <= obs['x'] < 13.5
    hurdle = obs['x'] >= 13.0
    # 摆腿先缩短越障，再伸出接地；上坡缩短落脚腿长。
    if hurdle:
        length = 0.55 if dt < 0.22 else 0.86
    elif dt < (0.13 if quick else 0.15):
        length = 0.62
    elif uphill:
        length = 0.82
    else:
        length = 0.895 if obs['x'] < 8.8 else 0.88
    flex = 2 * math.acos(length / 0.9)
    knee = (1.003564 - flex) / 0.916298
    if hurdle and not downhill:
        foot_x = 0.28 * obs['vx'] - 0.22
    elif downhill:
        foot_x = 0.25 * obs['vx'] - 0.16
    else:
        foot_x = 0.25 * obs['vx'] - 0.24
    foot_x = max(-0.35, min(0.55, foot_x))
    world = math.asin(max(-0.7, min(0.7, (foot_x + 0.3 * math.sin(a)) / length)))
    swing = (a + world + flex / 2 - 0.1309) / 1.178097
    # 坡顶提前回正；跨栏长度控制与坡顶姿态控制允许重叠。
    if downhill:
        lean = 0.15
    elif uphill:
        lean = 0.35
    elif hurdle:
        lean = 0.25
    else:
        lean = 0.35 if obs['x'] > 1.0 else 0.25
    stance = obs['joints'][2 * side] - 0.8 * (a - lean) - 0.2 * w
    push = (0.1 + 0.9 * min(1.0, dt / 0.22)) if uphill else 0.9
    legs = [stance, push, swing, knee] if side == 0 else [swing, knee, stance, push]
    return [max(-1.0, min(1.0, value)) for value in legs + [-0.4, -0.4]]
