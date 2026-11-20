START_X = 9.0
flights = 0
was_air = False
locked = False

def act(obs):
    global flights, was_air, locked
    t = obs["time"]
    contact = obs["contact"]
    touching = any(contact.values())
    if t >= 1.20:
        if not touching and not was_air:
            was_air = True
            flights += 1
        elif touching and was_air:
            was_air = False
            if flights >= 2:
                locked = True
    if t < 1.20:
        return [0.0, 1.0, 0.0, 1.0, -0.39, -0.39, 0.0, 0.0]
    if t < 1.35:
        return [0.5, 1.0, 0.5, 1.0, -0.39, -0.39, -1.0, -1.0]
    if locked:
        return [-0.930, -0.975, -0.930, -0.975, -0.225, -0.225, 1.0, 1.0]
    if not contact["foot_L"] and not contact["foot_R"]:
        return [-0.930, -0.975, -0.930, -0.975, -0.225, -0.225, 1.0, 1.0]
    attempt = obs.get("attempt", 1)
    if attempt == 2:
        return [0.5, 0.0, 0.5, 0.0, -0.39, -0.39, -1.0, -1.0]
    if attempt == 3:
        return [-0.5, -0.5, -0.5, -0.5, 0.5, 0.5, -1.0, -1.0]
    if attempt == 5:
        return [0.0, 0.0, 0.0, 0.0, 0.5, 0.5, 1.0, 1.0]
    return [-0.5, 0.5, -0.5, 0.5, 0.5, 0.5, -1.0, -1.0]
