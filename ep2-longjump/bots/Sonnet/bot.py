START_X = 9.0
P = {'tc': 0.942, 'ch': 0.695, 'ck': 0.421, 'ca': 0.583, 'cs': -1, 'eh': 0.148, 'es': -0.092, 'ea': -1, 'tuck': 0.502, 'lh': 0.487, 'lk': 1, 'ls': -0.728, 'la': -1}
S = {'ph': 0, 't0': 0.0}
def act(obs):
    p = P
    t = obs['time']; c = obs['contact']
    air = not (c['foot_L'] or c['foot_R'] or c['torso'] or c['hand_L'] or c['hand_R'])
    ph = S['ph']
    if ph == 0:
        if t > 0.4:
            S['ph'] = 1; S['t0'] = t
        return [-.11, 1, -.11, 1, -.39, -.39, -.09, -.09]
    if ph == 1:
        f = min(1, (t - S['t0']) / p['tc'])
        h = -.11 + (p['ch'] + .11) * f; k = 1 + (p['ck'] - 1) * f; a = -.09 + (p['ca'] + .09) * f
        sh = -.39 + (p['cs'] + .39) * f
        if f >= 1:
            S['ph'] = 2; S['t0'] = t
        return [h, k, h, k, sh, sh, a, a]
    if ph == 2:
        if air:
            S['ph'] = 3; S['t0'] = t
        if t - S['t0'] > 1.5:
            S['ph'] = 3
        return [p['eh'], 1, p['eh'], 1, p['es'], p['es'], p['ea'], p['ea']]
    tt = t - S['t0']
    if tt < p['tuck']:
        return [-.93, -.975, -.93, -.975, -.225, -.225, 1, 1]
    return [p['lh'], p['lk'], p['lh'], p['lk'], p['ls'], p['ls'], p['la'], p['la']]
