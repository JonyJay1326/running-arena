import math

_w = 0.0
_stop_pose = None
_offset = 0.0
_brace_until = 0.0
_crouch = 0.0
_fallen = None
_last_side = -1
_start_x = 0.0
_start_y = 0.0
_target_x = 0.0
_target_y = 0.0


def clip(v, low=-1.0, high=1.0):
    return max(low, min(high, v))


def ground(x):
    return max(0.0, min(x - 9.0, 13.0 - x, 2.0)) * math.tan(math.pi / 12)


def leg_ik(dx, down, a):
    length = clip(math.sqrt(dx * dx + down * down), 0.25, 0.898)
    flex = 2.0 * math.acos(length / 0.90)
    hip = math.atan2(dx, down) + 0.5 * flex + a
    return clip((hip - math.radians(7.5)) / math.radians(67.5)), clip((math.radians(57.5) - flex) / math.radians(52.5))


def act(obs):
    global _w, _stop_pose, _offset, _brace_until, _crouch, _fallen, _last_side, _start_x, _start_y, _target_x, _target_y
    t = obs['time']
    a = obs['torso_angle']
    if _stop_pose is None and obs['x'] >= 18.1:
        _stop_pose = list(obs['joints'])
    if _stop_pose is not None:
        return _stop_pose
    _w += 0.15 * (obs['torso_omega'] - _w)
    if _fallen is not None:
        return _fallen
    if obs['contact']['torso'] or obs['y'] < 0.50:
        _fallen = list(obs['joints'])
        return _fallen
    if obs['x'] > 10.3 and a > 0.85 and obs['y'] > 0.75:
        _brace_until = t + (0.12 if obs['x'] < 11.4 else 0.02)
    if t < _brace_until:
        h = -0.55 if obs['x'] < 11.4 else -0.95
        return [h, 1.0, h, 1.0, -1.0, -1.0]
    if t < 0.25:
        h = clip(0.30 - 0.8 * a)
        return [h, 1.0, h, 1.0, -0.39, -0.39]
    cycle = (t - 0.25 - _offset) / 0.40
    side = int(cycle) % 2
    swing = 1 - side
    p = cycle % 1.0
    if obs['x'] > 12.5 and obs['contact']['foot_L'] != obs['contact']['foot_R']:
        actual = 0 if obs['contact']['foot_L'] else 1
        if side != actual:
            side = actual
            swing = 1 - side
            p = 0.0
            _offset = t - 0.25 - 0.40 * side
            _last_side = -1
    hx = obs['x'] - 0.30 * math.sin(a)
    hy = ground(obs['x']) + obs['y'] - 0.30 * math.cos(a)
    if side != _last_side:
        _last_side = side
        q = math.radians(7.5 + 67.5 * obs['joints'][2 * swing]) - a
        flex = math.radians(57.5 - 52.5 * obs['joints'][2 * swing + 1])
        _start_x = hx + 0.45 * math.sin(q) + 0.45 * math.sin(q - flex)
        _start_y = hy - 0.45 * math.cos(q) - 0.45 * math.cos(q - flex)
        _target_x = obs['x'] + clip(0.58 * obs['vx'] - 0.08, -0.25, 1.40 if obs['x'] > 10.4 else 0.70)
        _target_y = ground(_target_x) + 0.04
        for bx in (5.5, 6.5, 7.5, 8.5):
            if abs(_target_x - bx) < 0.23:
                _target_y += 0.08
    blend = p * p * (3.0 - 2.0 * p)
    fx = _start_x + (_target_x - _start_x) * blend
    fy = _start_y + (_target_y - _start_y) * blend + (0.07 if 8.6 < obs['x'] < 13.0 else 0.18) * math.sin(math.pi * p)
    on_slope = 8.6 < obs['x'] < 13.0
    crouch_target = 1.0 if on_slope and obs['x'] < 12.5 else 0.0
    release = 0.20 if obs['x'] > 12.5 else 0.02
    _crouch += clip(crouch_target - _crouch, -release, 0.02)
    target_a = clip(0.20 + 0.30 * (1.0 - obs['vx']), 0.02, 0.60)
    if on_slope:
        crest = clip((obs['x'] - 10.4) / 0.8, 0.0, 1.0)
        target_a = clip(0.40 + 0.15 * (1.0 - obs['vx']) - 0.40 * crest, -0.08, 0.65)
    action = []
    for i in range(2):
        if i == side:
            hip = obs['joints'][2 * i] + 0.65 * (target_a - a) - 0.07 * _w
            knee = 1.0 - 1.20 * _crouch
        else:
            hip, knee = leg_ik(fx - hx, max(0.25, hy - fy), a)
        action += [clip(hip), clip(knee)]
    action += ([-1.0, -1.0] if obs['x'] > 11.4 else [-0.39, -0.39])
    return action
