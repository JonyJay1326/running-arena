import math
START_X = 3.0
P = {
    'G_w': 0.175616,
    'A_x0': 7.185392,
    'A_kx': 3.049733,
    'A_kp': 0.061724,
    'A_max': 0.159006,
    'G_wv': 0.021835,
    'G_lv': 0,
    'G_Av': 0.120218,
    'G_k2': -4.791249,
    'G_kd2': 2.325339,
    'G_a2': 7.384766,
    'G_ad2': -0.136019,
    'G_h0': 60.892194,
    'G_hA': 37.224192,
    'G_k0': -38.399171,
    'G_kA': 60.329183,
    'G_kd': -0.143376,
    'G_a0': 23.396188,
    'G_aA': 14.700223,
    'G_ad': -0.169227,
    'G_g': 0.976951,
    'G_lean': 2.681757,
    'G_gw': 0.800682,
    'G_sA': 34.744467,
    'G_ramp': 34,
    'G_h2': 29.120616,
    'G_hd2': -1.428675,
    'T_trig': 11.506532,
    'T_phase': 1.524626,
    'T_gate': 0.0,
    'T_h': -31.386099,
    'T_k': 0.438028,
    'T_a': -50.699524,
    'T_s': 86.625163,
    'T_max': 21,
    'T_pre': 0,
    'T_ph': 77.10304,
    'T_pk': -11.678606,
    'T_pa': 9.710823,
    'toe_off': 0.25,
    'stage': 3,
    'J_hc': 73,
    'J_kc': -74.824298,
    'J_ac': 58,
    'J_Tc': 34,
    'J_Th': 25,
    'J_he': -0.703902,
    'J_ke': -25.720491,
    'J_ae': 0,
    'J_dk': 6,
    'J_da': 4,
    'J_ha': 70.086758,
    'J_ka': -17.991758,
    'J_aa': 53.266886,
    'J_Ta': 15,
    'J_hl': 21.910497,
    'J_kl': 0.504556,
    'J_al': 19.31232,
    'J_sc': 13.300438,
    'J_se': 119.508836,
    'J_sa': -4.397741,
}
S = {'ph': 'run', 'n': 0, 'k': 0, 'phi': 0.0}

def _h(d): return (d + 60.0) / 135.0 * 2 - 1
def _k(d): return (d + 110.0) / 110.0 * 2 - 1
def _a(d): return (d + 50.0) / 110.0 * 2 - 1
def _s(d): return (d + 70.0) / 230.0 * 2 - 1

def pose(h, k, a, s=0.0):
    return [_h(h), _k(k), _h(h), _k(k), _s(s), _s(s), _a(a), _a(a)]

def act(obs):
    p = P; s = S
    s['k'] += 1; s['n'] += 1
    c = obs['contact']
    ground = c['foot_L'] or c['foot_R']
    ph = s['ph']
    if ph == 'run':
        if s['k'] <= 15:
            return pose(0, 0, 0, 0)
        if obs['y'] < 0.75 or abs(obs['torso_angle']) > 1.2:
            s['ph'] = 'dead'
            return pose(0, 0, 0, 0)
        if p['stage'] >= 2 and obs['x'] + p['toe_off'] >= p['T_trig']:
            a0 = (s['phi'] - p['T_phase']) % math.pi
            if p['T_gate'] < 0.5 or a0 < p['G_w'] * 1.01:
                s['ph'] = 'pre'; s['n'] = 0
        amp = min(1.0, (s['k'] - 15) / max(1.0, p['G_ramp']))
        v = max(0.0, obs['vx'])
        s['phi'] += p['G_w'] + p['G_wv'] * v
        toe = obs['x'] + p['toe_off']
        if toe > p['A_x0']:
            e = (p['T_phase'] - p['A_kx'] * (p['T_trig'] - toe) - s['phi'] + math.pi / 2) % math.pi - math.pi / 2
            s['phi'] += max(-p['A_max'], min(p['A_max'], p['A_kp'] * e))
        fb = p['G_g'] * (obs['torso_angle'] * 57.3 - p['G_lean'] - p['G_lv'] * v) + p['G_gw'] * obs['torso_omega'] * 5.73
        amp = amp * (1.0 + p['G_Av'] * v)
        out = [0.0] * 8
        for i in (0, 1):
            f = s['phi'] + math.pi * i
            hip = p['G_h0'] + amp * (p['G_hA'] * math.sin(f) + p['G_h2'] * math.sin(2 * f + p['G_hd2'])) - fb
            knee = p['G_k0'] - amp * p['G_kA'] * max(0.0, math.sin(f + p['G_kd'])) - p['G_k2'] * max(0.0, math.sin(f + p['G_kd2']))
            ank = p['G_a0'] + amp * p['G_aA'] * math.sin(f + p['G_ad']) + p['G_a2'] * math.sin(2 * f + p['G_ad2'])
            out[2 * i] = _h(hip); out[2 * i + 1] = _k(knee); out[6 + i] = _a(ank)
            out[4 + i] = _s(-amp * p['G_sA'] * math.sin(f))
        return out
    if ph == 'dead':
        return pose(0, 0, 0, 0)
    if ph == 'pre':
        if s['n'] > p['T_pre']:
            s['ph'] = 'je'; s['n'] = 0
        return pose(p['T_ph'], p['T_pk'], p['T_pa'], -30)
    if ph == 'je':
        if (not ground and s['n'] > 2) or s['n'] > p['T_max']:
            s['ph'] = 'ja'; s['n'] = 0
        return pose(p['T_h'], p['T_k'], p['T_a'], p['T_s'])
    if ph == 'ja':
        if ground and s['n'] > 12:
            s['ph'] = 'jl'; s['n'] = 0
        return pose(p['J_ha'], p['J_ka'], p['J_aa'], p['J_sa'])
    return pose(p['J_hl'], p['J_kl'], p['J_al'], p['J_sa'])
