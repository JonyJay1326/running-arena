START_X = 8.006370230610896
begin = None
extension = None
push_t = None

def act(obs):
    global begin, extension, push_t
    if begin is None:
        begin = obs['time']
    elapsed = obs['time'] - begin
    if extension is None:
        ready = elapsed >= 0.1 and obs['torso_angle'] >= 0.7546589493428644 and obs['vy'] <= 1.2376814737466124
        if ready or elapsed >= 0.9307805454186108:
            extension = obs['time']
        else:
            return [1.0, -0.874, 1.0, -0.874, -0.002677241115502542, -0.002677241115502542, 1.0, 1.0]
    te = obs['time'] - extension
    if te < 0.11554337601804723:
        return [-0.43879017502862594, 0.637748860416271, -0.43879017502862594, 0.637748860416271, 0.4264382288054981, 0.4264382288054981, -0.45803587384858824, -0.45803587384858824]
    if push_t is None and (obs['contact']['foot_L'] or obs['contact']['foot_R']) and obs['dist_to_line'] < 0.9 and elapsed > 1.2:
        push_t = obs['time']
    if push_t is not None and obs['time'] - push_t < 0.14:
        return [-0.6, 1.0, -0.6, 1.0, 1.0, 1.0, -0.2, -0.2]
    return [-0.9072726586918791, 0.31306698161835045, -0.9072726586918791, 0.31306698161835045, 0.9985393933475413, 0.9985393933475413, -0.47058258530610775, -0.47058258530610775]
