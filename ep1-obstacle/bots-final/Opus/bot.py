import math

# SIMBICON-style walker: stance hip balances torso, swing hip places foot by
# COM-to-stance-foot distance + velocity feedback.
D = 180.0 / math.pi

state = {'stance': 0, 't0': 0.0}

P = {
    'T': 0.30,          # step duration (s)
    'tmax': 3.0,        # forced switch (in units of T)
    'lean': 0.0,        # desired torso lean (deg, forward +)
    'swing0': 5.0,     # swing thigh world angle base (deg, forward +)
    'cd': 0.0,         # deg per m of COM ahead of stance foot
    'cv': 20.0,         # deg per m/s
    'kw': 0.10,
    'vdes': 1.0,
    'ki': 0.0,
    'st_min': -99.0,
    'st_max': 99.0,
    'stop_x': 18.3,
    'stop_h1': 35.0,
    'stop_h2': -25.0,
    'always_near': 0,
    'swrel': 50.0,
    'nb_back': 0.5,
    'nb_fwd': 0.7,
    'vcorr': 0.0,
    'go_v': 0.3,
    'go_lean': 12.0,
    'v0': 0.3,
    'vramp': 99.0,
    'fa': 0.0,
    'fw': 0.0,
    'k1': 10.0,
    'air_g': 0.3,
    'dbg': None,
    'dmax': 9.0,
    'swmax': 60.0,
    'swmin': -30.0,
    'arm': 0.0,         # torso omega damping (deg per deg/s)
    'up_lean': 8.0,
    'up_sw': -5.0,
    'dn_lean': -4.0,
    'dn_sw': 0.0,
    'hd_lean': 0.0,
    'hd_sw': 0.0,
    'stance_knee': -20.0,
    'swing_knee': -65.0,
}


def hip_u(deg):
    return (deg - 7.5) / 67.5


def knee_u(deg):
    return (deg + 110.0) / 52.5 - 1.0


def hip_deg(u):
    return 67.5 * u + 7.5


def knee_deg(u):
    return (u + 1.0) * 52.5 - 110.0


def foot_x(j, side, a):
    """ankle x relative to torso centre (m)."""
    pt = (hip_deg(j[2 * side]) - a) / D
    ps = pt + knee_deg(j[2 * side + 1]) / D
    return -0.30 * math.sin(a / D) + 0.45 * math.sin(pt) + 0.45 * math.sin(ps)


P0 = dict(P)

# overrides used on the obstacle-free sprint track
PF = {'vdes': 1.25, 'swrel': 32.0}
# overrides used on the obstacle track
PO = {'hd_lean': 6.18, 'dn_lean': -4.5, 'f2_lean': 1.0, 'b_lean': -3.0, 'up_swrel': 39.0, 'fl_x': 14.5, 'fl_lean': 5.0, 'hd_x': 12.6, 'fl2_x': 15.8, 'fl2_lean': 10.0}


def act(obs):
    t = obs['time']
    a = obs['torso_angle'] * D
    w = obs['torso_omega'] * D
    j = obs['joints']
    c = obs['contact']
    st = state
    global P
    if t == 0.0:
        st.clear()
        st['stance'] = 0
        st['t0'] = 0.0
        st['obst'] = obs['next_bump_dist'] < 50
        P = dict(P0)
        P.update(PO if st['obst'] else PF)
    x = obs['x']

    if not st.get('go'):
        if obs['vx'] > P['go_v'] or t > 1.5:
            st['go'] = 1
            st['t0'] = t
        else:
            out = [0.0] * 6
            for i in (0, 1):
                out[2 * i] = hip_u(hip_deg(j[2 * i]) - (a - P['go_lean']) - P['kw'] * w)
                out[2 * i + 1] = knee_u(-8.0)
            out[4] = out[5] = -0.39
            return out
    if x > P['stop_x'] and not st.get('stop'):
        st['stop'] = t
    if st.get('stop'):
        # past the finish: wide stance, knees bent, drop and stay put
        out = [hip_u(P['stop_h1'] + a), knee_u(-30.0), hip_u(P['stop_h2'] + a), knee_u(-30.0), -0.39, -0.39]
        return out
    s = st['stance']
    phase = (t - st['t0']) / P['T']
    sw = 1 - s
    swing_foot = c['foot_L'] if sw == 0 else c['foot_R']
    if phase > P['tmax'] or (phase > 0.5 and swing_foot):
        st['stance'] = sw
        st['t0'] = t
        s, sw = sw, s
        phase = 0.0

    st['vf'] = st.get('vf', 0.0) + (obs['vx'] - st.get('vf', 0.0)) * 0.02
    st['adj'] = max(-12.0, min(12.0, st.get('adj', 0.0) + P['ki'] * (st['vf'] - P['vdes']) / 60.0))
    lean = P['lean']
    sw0 = P['swing0'] + st['adj']
    if st.get('obst'):
        if P.get('f2x', 2.5) <= x < 5.0:
            lean += P.get('f2_lean', 0.0)
        if 5.0 <= x < 9.0:
            lean += P.get('b_lean', 0.0)
        if 9.0 <= x < P.get('dn_x', 11.0):
            lean += P['up_lean']
            sw0 += P['up_sw']
        elif P.get('dn_x', 11.0) <= x < P.get('hd_x', 13.0):
            lean += P['dn_lean']
            sw0 += P['dn_sw']
        elif x >= P.get('hd_x', 13.0):
            lean += P['hd_lean']
            sw0 += P['hd_sw']
        if x >= P.get('fl_x', 99.0):
            lean += P.get('fl_lean', 0.0)
        if x >= P.get('fl2_x', 99.0):
            lean += P.get('fl2_lean', 0.0)

    # COM proxy ~0.1m above hip: remove torso pitching from position/velocity
    d = -foot_x(j, s, a) - 0.20 * math.sin(a / D)
    vx = obs['vx'] - P['vcorr'] * math.cos(a / D) * obs['torso_omega']

    out = [0.0] * 6
    rel_st = hip_deg(j[2 * s])
    st_foot = c['foot_L'] if s == 0 else c['foot_R']
    g = 1.0 if st_foot else P['air_g']
    tgt_st = rel_st - g * ((a - lean) + P['kw'] * w)
    d = max(-P['dmax'], min(P['dmax'], d))
    vdes = P['vdes']
    if st.get('obst') and x < 5.0:
        vdes = P.get('f_vdes', vdes)
    elif st.get('obst') and x < 9.0:
        vdes = P.get('b_vdes', vdes)
    sw_world = sw0 + P['cd'] * d + P['k1'] * vx + P['cv'] * (vx - min(vdes, P['v0'] + P['vramp'] * t))
    sw_world += P['fa'] * max(0.0, a - lean - 5.0) + P['fw'] * max(0.0, w)
    sw_world = max(P['swmin'], min(P['swmax'], sw_world))
    tgt_sw = sw_world + a
    out[2 * s] = hip_u(max(P['st_min'], min(P['st_max'], tgt_st)))
    swrel = P['swrel']
    if st.get('obst') and x >= 13.0:
        swrel = P.get('hd_swrel', swrel)
    elif st.get('obst') and x >= P.get('dn_x', 11.0):
        swrel = P.get('dn_swrel', swrel)
    elif st.get('obst') and x >= 9.0:
        swrel = P.get('up_swrel', swrel)
    if x >= P.get('fl_x', 99.0):
        swrel = P.get('fl_swrel', swrel)
    out[2 * sw] = hip_u(min(swrel, tgt_sw))
    out[2 * s + 1] = knee_u(P['stance_knee'])
    nb = obs['next_bump_dist']
    if nb < 50:
        bx = round(x + nb, 2)
        bl = st.setdefault('bumps', [])
        if not bl or abs(bl[-1] - bx) > 0.2:
            bl.append(bx)
    near = P['always_near'] or (st.get('obst') and 8.9 < x < 11.0) or any(-P['nb_back'] < b - x < P['nb_fwd'] for b in st.get('bumps', []))
    kp = 0.8 if near else 0.6
    out[2 * sw + 1] = knee_u((P['swing_knee'] - (20.0 if near else 0.0)) if phase < kp else -10.0)
    arm = P['arm'] * (j[2 * sw] - j[2 * s])
    out[4 + sw] = -0.39 - arm
    out[4 + s] = -0.39 + arm
    if P.get('dbg'):
        t0, t1 = P['dbg']
        if t0 <= t < t1 and int(round(t * 60)) % 4 == 0:
            st.setdefault('log', []).append('%.2f x%.2f y%.2f a%+.0f w%+.0f v%+.2f s%d ph%.1f d%+.2f hs%+.0f hw%+.0f ks%+.0f kw%+.0f tsw%+.0f c%d%d' % (
                t, x, obs['y'], a, w, vx, s, phase, d, hip_deg(j[2 * s]), hip_deg(j[2 * sw]), knee_deg(j[2 * s + 1]), knee_deg(j[2 * sw + 1]), sw_world, c['foot_L'], c['foot_R']))
        if t >= t1 and not st.get('dumped'):
            st['dumped'] = 1
            raise ValueError(' | '.join(st['log']))
    return out
