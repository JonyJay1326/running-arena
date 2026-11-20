import math

T_STEP = 0.32
st = {}


def _reset():
    st['init'] = False
    st['last_t'] = -1.0
    st['S'] = 0
    st['W'] = 1
    st['t0'] = 0.0
    st['land'] = 0.6
    st['sw_hip'] = 0.3
    st['hip'] = [0.3, 0.3]
    st['down'] = False
    st['td'] = 0.0


_reset()


def _is_down(obs):
    if c['torso'] or obs['y'] < 0.50:
        return True
    return abs(obs['torso_angle']) > 0.85 and obs['y'] < 0.72


def act(obs: dict) -> list[float]:
    t = obs['time']
    if t < st['last_t']:
        _reset()
    st['last_t'] = t
    th = obs['torso_angle']
    om = obs['torso_omega']

    if t < 0.30:
        return [0.3, 0.9, 0.3, 0.9, -0.39, -0.39]
    if not st['init']:
        st['init'] = True
        st['t0'] = t
        st['S'], st['W'] = 0, 1
        st['land'] = 0.3
        st['sw_hip'] = 0.3

    tau = (t - st['t0']) / T_STEP
    if tau >= 1.0:
        oldS = st['S']
        st['S'], st['W'] = st['W'], oldS
        st['t0'] = t
        st['land'] = st['sw_hip']
        tau = 0.0

    S, W = st['S'], st['W']
    pd = 0.3 - 1.2 * (th - 0.48) - 0.3 * om
    pd = max(-1.0, min(1.0, pd))
    blend = min(1.0, (t - st['t0']) / 0.15)
    hipS = st['land'] + (pd - st['land']) * blend
    kneeS = 0.9


    if tau < 0.30:
        u = tau / 0.30
        hipW = st['hip'][W] * (1 - u) + 0.75 * u
        kneeW = 0.9 - 1.5 * math.sin(math.pi * u)
    else:
        hipW = 0.75
        kneeW = 0.9
    st['sw_hip'] = hipW
    st['hip'][W] = hipW

    out = [0.0] * 4
    out[2 * S] = hipS
    out[2 * S + 1] = kneeS
    out[2 * W] = hipW
    out[2 * W + 1] = kneeW
    if 11.5 < t < 13.2:
        out[2 * S] = -0.75
        out[2 * W] = -0.75
    if t > 12.5 and (obs['contact']['torso'] or (th > 0.95 and obs['y'] < 0.62)):
        return [0.3, 0.9, 0.3, 0.9, -0.39, -0.39]
    return out + [-0.39, -0.39]
