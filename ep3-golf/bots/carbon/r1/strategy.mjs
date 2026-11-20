// bot/strategy.mjs — 逆弹道精确求解器、多弹种全物理预演、防炸鸡、挖坑开路与纸飞机决策
import {
  PHYS,
  carveCrater,
  checkSupportBelow,
  cloneTerrain,
  computeExplosionEffect,
  decodeTerrain,
  estimateFootY,
  estimateGroundAngle,
  findColumnTopY,
  getActorMuzzle,
  getMuzzlePosition,
  getObjectShape,
  getStandCandidates,
  hasHeadroom,
  isObjectActive,
  isSolidPixel,
  simulateBodyImpulse,
  simulateProjectile,
} from './physics.mjs';

const DEFAULT_ATTACKS = {
  single: { damage: 120, radius: 32, damageRadius: 30, offsets: [0] },
  burst: { damage: 65, radius: 26, damageRadius: 30, offsets: [0, 0, 0] },
  spread: { damage: 55, radius: 24, damageRadius: 30, offsets: [-6, 0, 6] },
  dig: { damage: 30, radius: 60, damageRadius: 30, offsets: [0] },
};

function getAttackSpec(kind, config) {
  const cfg = config?.ATTACKS?.[kind];
  if (!cfg) return DEFAULT_ATTACKS[kind];
  const count = kind === 'burst' ? (cfg.ticks?.length || 3) : 1;
  const offsets = kind === 'burst' ? Array(count).fill(0) : (cfg.offsets || [0]);
  return {
    kind,
    damage: cfg.damage,
    radius: cfg.radius,
    damageRadius: cfg.damageRadius ?? 30,
    offsets,
    kindScale: kind === 'dig' ? 1.05 : kind === 'single' ? 1.0 : 0.75,
  };
}

// Fast inverse-ballistics solver: find power in [2, 100] at given (stand, angle, dir)
// to pass as close as possible to (tx, ty).
export function solvePowerForAngle(stand, angle, dir, tx, ty, wind, terrain, objects, config) {
  const mx = stand.muzzle.x;
  const my = stand.muzzle.y;
  const actualAngle = angle + dir * (stand.groundAngle || 0);
  const rad = (actualAngle * Math.PI) / 180;
  const cosA = Math.cos(rad);
  const sinA = Math.sin(rad);

  const dxDir = (tx - mx) * dir;
  const dy = ty - my;

  // Estimate initial power from zero-wind parabola
  let pGuess = 55;
  if (dxDir > 4 && Math.abs(cosA) > 0.06) {
    const denom = (dy + dxDir * (sinA / cosA)) * cosA * cosA;
    if (denom > 1.5) {
      const R = (0.5 * dxDir * dxDir) / denom;
      const p0 = Math.sqrt(R / 0.120125);
      if (Number.isFinite(p0)) {
        // Compensate roughly for wind
        const tEst = dxDir / Math.max(40, 15.5 * Math.min(100, Math.max(5, p0)) * Math.abs(cosA));
        const windShift = 0.5 * 75 * wind * dir * tEst * tEst;
        const dxAdj = Math.max(8, dxDir - windShift);
        const denomAdj = (dy + dxAdj * (sinA / cosA)) * cosA * cosA;
        if (denomAdj > 1.5) {
          const p1 = Math.sqrt((0.5 * dxAdj * dxAdj) / denomAdj / 0.120125);
          if (Number.isFinite(p1)) pGuess = p1;
        } else {
          pGuess = p0;
        }
      }
    } else {
      return null; // Target above tangent line
    }
  } else if (dxDir <= -15 && wind * dir >= 0) {
    return null; // Wrong direction unless blown back by strong opposing wind
  }

  if (pGuess < 1 || pGuess > 118) return null;
  pGuess = Math.max(3, Math.min(99, pGuess));

  // Local refinement around pGuess using fast substepFactor=6, then exact=1
  let bestP = pGuess;
  let bestDist = Infinity;

  const evalDist = (p, sub) => {
    const res = simulateProjectile({
      startX: mx,
      startY: my,
      actualAngleDeg: actualAngle,
      power: p,
      dir,
      wind,
      terrain,
      objects,
      config,
      substepFactor: sub,
    });
    return { dist: Math.hypot(res.x - tx, res.y - ty), res };
  };

  for (const delta of [-8, -3, 0, 3, 8]) {
    const p = Math.max(2, Math.min(100, pGuess + delta));
    const { dist } = evalDist(p, 6);
    if (dist < bestDist) {
      bestDist = dist;
      bestP = p;
    }
  }

  // Coordinate pattern search (steps 1.8 -> 0.5 -> 0.15)
  for (const step of [1.8, 0.5, 0.15]) {
    const sub = step > 0.6 ? 4 : 1;
    let cur = evalDist(bestP, sub).dist;
    bestDist = cur;
    for (const sgn of [-1, 1]) {
      const candP = Math.max(1, Math.min(100, bestP + sgn * step));
      const d = evalDist(candP, sub).dist;
      if (d < bestDist) {
        bestDist = d;
        bestP = candP;
      }
    }
  }

  const finalP = Math.round(bestP * 100) / 100;
  const exact = evalDist(finalP, 1);
  return {
    angle: Math.round(angle * 100) / 100,
    power: finalP,
    dir,
    dist: exact.dist,
    impact: exact.res,
  };
}

// Count solid cells blocking recycle zone interior & entry
export function countRecycleObstruction(t, recycle, bugX) {
  if (!recycle) return { count: 0, digTarget: null };
  const x0 = Math.max(8, recycle.x0 + 12);
  const x1 = Math.min(t.width * t.cell - 8, recycle.x1 - 12);
  // Bug center must rest in [recycle.y0, recycle.y1], so ground surface inside recycle
  // should be in [recycle.y0 + 14, recycle.y1 + 14], and air above up to recycle.y0 - 16 should be clear.
  const yAirTop = Math.max(8, recycle.y0 - 14);
  const yAirBot = Math.min(t.height * t.cell - 12, Math.min(recycle.y1 + 6, recycle.y0 + 34));

  let solidCount = 0;
  let sumX = 0, sumY = 0;
  // Prefer side closer to Bug first so we open the entrance too
  for (let x = x0; x <= x1; x += 4) {
    for (let y = yAirTop; y <= yAirBot; y += 4) {
      if (isSolidPixel(t, x, y)) {
        solidCount++;
        sumX += x;
        sumY += y;
      }
    }
  }
  if (solidCount === 0) return { count: 0, digTarget: null };
  return {
    count: solidCount,
    digTarget: { x: sumX / solidCount, y: sumY / solidCount },
  };
}

export function isBugRestingInRecycle(bugObj, recycle, config) {
  if (!recycle || !bugObj) return false;
  const r = bugObj.radius ?? config?.OBJECTS?.bug?.radius ?? 14;
  const cx = bugObj.x;
  const cy = bugObj.y - r;
  return cx >= recycle.x0 && cx <= recycle.x1 && cy >= recycle.y0 && cy <= recycle.y1;
}

// Full multi-projectile turn simulation
export function simulateFullTurn(stand, attack, hole, baseTerrain, baseObjects, config, cal) {
  const spec = getAttackSpec(attack.kind, config);
  const simT = cloneTerrain(baseTerrain);
  const simObjs = baseObjects.map(o => ({ ...o }));

  const impacts = [];
  let totalCarved = 0;
  let chickenPenalties = 0;
  let bugOutPenalties = 0;
  let bugBounces = 0;
  let bugLaunchSpeed = 0;
  let multiShotBugClip = false;

  for (let pIdx = 0; pIdx < spec.offsets.length; pIdx++) {
    const offset = spec.offsets[pIdx];
    const actualAngle = attack.angle + attack.dir * (stand.groundAngle || 0) + offset;
    const imp = simulateProjectile({
      startX: stand.muzzle.x,
      startY: stand.muzzle.y,
      actualAngleDeg: actualAngle,
      power: attack.power,
      dir: attack.dir,
      wind: hole.wind || 0,
      terrain: simT,
      objects: simObjs,
      config,
      substepFactor: 1,
    });
    impacts.push(imp);

    if (imp.hit === 'out' || imp.hit === 'timeout') continue;

    // Snapshot pre-crater terrain for splash LOS, then carve crater
    const preT = simT;
    // Compute splash & impulse before carving (or if direct hit, LOS is true anyway)
    const effects = [];
    for (let i = 0; i < simObjs.length; i++) {
      const o = simObjs[i];
      if (!isObjectActive(o)) continue;
      const eff = computeExplosionEffect(imp.x, imp.y, spec, o, preT, preT, config, cal);
      effects.push({ o, eff });
    }

    totalCarved += carveCrater(simT, imp.x, imp.y, spec.radius);

    // Apply damage and movement
    for (const { o, eff } of effects) {
      if (o.type === 'chicken') {
        if (eff.damage > 0.01 || imp.objectId === o.id) {
          o.stunned = true;
          o.removed = true;
          chickenPenalties++;
        }
        continue;
      }

      if (o.type === 'token') {
        if (eff.damage > 0.01 || imp.objectId === o.id) {
          o.hp = 0;
          o.broken = true;
        }
      } else if (o.type === 'bigToken' || o.type === 'pushToken') {
        const dmg = imp.objectId === o.id ? Math.max(eff.damage, spec.damage * 0.98) : eff.damage;
        if (dmg > 0) {
          o.hp = Math.max(0, (o.hp ?? 150) - dmg);
          if (o.hp <= 0.05) {
            o.hp = 0;
            o.broken = true;
          }
        }
      }

      // Knockback & ballistic+slide for bug and surviving pushToken
      if (!o.broken && (o.type === 'bug' || o.type === 'pushToken') && Math.hypot(eff.vx0, eff.vy0) > 6) {
        if (o.type === 'bug' && spec.offsets.length > 1) {
          // Never allow multi-projectile burst/spread to mid-air juggle Bug unpredictably!
          multiShotBugClip = true;
        }
        const prevX = o.x;
        const prevY = o.y;
        const rOrW = o.type === 'bug' ? (o.radius ?? 14) : ((o.width ?? 30) * 0.5);
        const slideK = o.type === 'bug' ? (cal?.bugSlideK ?? 0.65) : (cal?.pushSlideK ?? 0.45);
        const bodyG = cal?.bodyGravity ?? 1000;
        const slid = simulateBodyImpulse(simT, o.x, o.y, rOrW, eff.vx0, eff.vy0, slideK, bodyG, config);
        if (o.type === 'bug') {
          bugBounces += slid.bounces || 0;
          bugLaunchSpeed = Math.max(bugLaunchSpeed, Math.hypot(eff.vx0, eff.vy0));
        }
        if (slid.fellOut) {
          if (o.type === 'bug') {
            bugOutPenalties++;
            o.x = prevX;
            o.y = prevY;
          } else {
            // pushToken falling out of map counts as broken!
            o.broken = true;
            o.removed = true;
          }
        } else {
          o.x = slid.x;
          o.y = slid.y;
        }
      }
    }

    // Gravity settle for any active objects whose support was carved out
    for (let i = 0; i < simObjs.length; i++) {
      const o = simObjs[i];
      if (!isObjectActive(o)) continue;
      const hw = o.type === 'bug' ? (o.radius ?? 14) : ((o.width ?? 28) * 0.5);
      const supY = checkSupportBelow(simT, o.x, Math.max(0, o.y - 4), hw);
      if (supY == null) {
        if (o.type === 'chicken') {
          o.stunned = true;
          o.removed = true;
          chickenPenalties++;
        } else if (o.type === 'bug') {
          // Reset to base object pos before this stroke
          const orig = baseObjects.find(b => b.id === o.id);
          if (orig) {
            o.x = orig.x;
            o.y = orig.y;
          }
          bugOutPenalties++;
        } else {
          // Any token falling out of map counts as broken!
          o.broken = true;
          o.removed = true;
        }
      } else if (supY > o.y) {
        o.y = supY;
      }
    }
  }

  // Update bug inRecycle status at end of stroke
  for (const o of simObjs) {
    if (o.type === 'bug' && !o.removed) {
      o.inRecycle = isBugRestingInRecycle(o, hole.recycle, config);
    }
  }

  return {
    impacts,
    simT,
    simObjs,
    totalCarved,
    chickenPenalties,
    bugOutPenalties,
    bugBounces,
    bugLaunchSpeed,
    multiShotBugClip,
  };
}

function isTokenType(type) {
  return type === 'token' || type === 'bigToken' || type === 'pushToken';
}

export function scoreSimulatedTurn(sim, stand, attack, obs, baseTerrain, recycleObsBefore, stalledPoints, config) {
  const { hole, objects: beforeObjs } = obs;
  const needTokens = hole.goal === 'tokens' || hole.goal === 'both';
  const needBug = hole.goal === 'bug' || hole.goal === 'both';

  const penalties = sim.chickenPenalties + sim.bugOutPenalties;

  let remTokensBefore = 0;
  let remTokensAfter = 0;
  let tokenScore = 0;

  for (let i = 0; i < beforeObjs.length; i++) {
    const b = beforeObjs[i];
    const a = sim.simObjs[i];
    if (!isTokenType(b.type) || b.broken || b.removed) continue;
    remTokensBefore++;
    if (a.broken || a.removed) {
      tokenScore += 520;
    } else {
      remTokensAfter++;
      if (needTokens) {
        const hpB = b.hp ?? 1;
        const hpA = a.hp ?? 1;
        const dmg = Math.max(0, hpB - hpA);
        if (dmg > 0) {
          const shotsB = Math.ceil(hpB / 120);
          const shotsA = Math.ceil(hpA / 120);
          tokenScore += (shotsB - shotsA) * 230 + dmg * 1.45;
        }
        // Avoid knocking surviving pushToken into a deep closed pit
        if (b.type === 'pushToken' && a.y - b.y > 24) {
          tokenScore -= 190;
        }
      }
    }
  }
  const brokenThisTurn = remTokensBefore - remTokensAfter;
  if (needTokens && brokenThisTurn >= 2) {
    tokenScore += (brokenThisTurn - 1) * 180; // Multi-kill efficiency bonus
  }

  let bugDoneAfter = true;
  let bugScore = 0;
  const bugBefore = beforeObjs.find(o => o.type === 'bug' && !o.removed);
  const bugAfter = sim.simObjs.find(o => o.type === 'bug' && !o.removed);

  if (needBug && bugBefore) {
    if (bugBefore.inRecycle) {
      bugDoneAfter = bugAfter ? bugAfter.inRecycle : true;
      if (!bugDoneAfter) bugScore -= 1200; // Never knock an already-recycled bug out!
    } else {
      bugDoneAfter = Boolean(bugAfter && bugAfter.inRecycle);
      if (bugDoneAfter) {
        const rx = hole.recycle ? (hole.recycle.x0 + hole.recycle.x1) * 0.5 : bugAfter.x;
        const halfSpan = hole.recycle ? Math.max(20, (hole.recycle.x1 - hole.recycle.x0) * 0.5) : 100;
        const centerQuality = Math.max(0, 1 - Math.abs(bugAfter.x - rx) / halfSpan);
        const puttQuality = centerQuality * 55 - (sim.bugBounces || 0) * 35 - (sim.bugLaunchSpeed || 0) * 0.04;
        bugScore += 950 + puttQuality;
      } else if (bugAfter && hole.recycle) {
        const rx = (hole.recycle.x0 + hole.recycle.x1) * 0.5;
        const ry = Math.min(hole.recycle.y1 - 8, (hole.recycle.y0 + hole.recycle.y1) * 0.5);
        const r = bugBefore.radius ?? 14;
        const dBefore = Math.hypot(bugBefore.x - rx, bugBefore.y - r - ry);
        const dAfter = Math.hypot(bugAfter.x - rx, bugAfter.y - r - ry);
        const progress = dBefore - dAfter;
        bugScore += progress * 2.4;

        // Horizontal alignment inside recycle x-span even if y needs settling
        if (bugAfter.x >= hole.recycle.x0 + 14 && bugAfter.x <= hole.recycle.x1 - 14) {
          bugScore += 95;
        }
        // Penalize overshooting past recycle toward map edge
        const dirToRec = rx >= bugBefore.x ? 1 : -1;
        if (dirToRec > 0 && bugAfter.x > hole.recycle.x1 + 16) {
          bugScore -= 360 + (bugAfter.x - hole.recycle.x1) * 1.8;
        } else if (dirToRec < 0 && bugAfter.x < hole.recycle.x0 - 16) {
          bugScore -= 360 + (hole.recycle.x0 - bugAfter.x) * 1.8;
        }
        // Penalize dropping bug into a deep crater outside recycle
        if (
          (bugAfter.x < hole.recycle.x0 || bugAfter.x > hole.recycle.x1) &&
          bugAfter.y - bugBefore.y > 28
        ) {
          bugScore -= 160;
        }
      }
    }
  }

  const multiBugPen = sim.multiShotBugClip ? 1700 : 0;
  const standBonus = stand.isCurrent ? 28 : Math.abs(stand.groundAngle || 0) < 0.5 ? 8 : 0;
  const completed = (!needTokens || remTokensAfter === 0) && (!needBug || bugDoneAfter);
  if (completed && !sim.multiShotBugClip) {
    let finishTiebreak = standBonus;
    if (needBug && bugAfter && hole.recycle) {
      const rx = (hole.recycle.x0 + hole.recycle.x1) * 0.5;
      const halfSpan = Math.max(20, (hole.recycle.x1 - hole.recycle.x0) * 0.5);
      finishTiebreak += Math.max(0, 1 - Math.abs(bugAfter.x - rx) / halfSpan) * 55;
      finishTiebreak -= (sim.bugBounces || 0) * 35 + (sim.bugLaunchSpeed || 0) * 0.04;
    }
    return 12000 + finishTiebreak - sim.chickenPenalties * 1600 - sim.bugOutPenalties * 2500;
  }

  let score = standBonus - multiBugPen;
  if (needTokens) score += tokenScore;
  if (needBug) score += bugScore;

  // Penalize rule penalties heavily
  score -= sim.chickenPenalties * 1850;
  score -= sim.bugOutPenalties * 2300;

  // Excavation score when recycle is obstructed by terrain
  if (needBug && bugBefore && !bugBefore.inRecycle && recycleObsBefore.count > 4) {
    const afterObs = countRecycleObstruction(sim.simT, hole.recycle, bugBefore.x);
    const cleared = Math.max(0, recycleObsBefore.count - afterObs.count);
    if (cleared > 0) {
      score += cleared * 4.2;
    }
  }

  // Excavation / tunneling bonus toward shielded objectives when no direct hit scored
  if (score < 40 && sim.totalCarved > 0) {
    // Check proximity of impacts to nearest unfinished target
    const unfinished = beforeObjs.filter(o => {
      if (!isObjectActive(o)) return false;
      if (needTokens && isTokenType(o.type)) return true;
      if (needBug && o.type === 'bug' && !o.inRecycle) return true;
      return false;
    });
    let bestProx = Infinity;
    for (const imp of sim.impacts) {
      if (imp.hit === 'out') continue;
      for (const u of unfinished) {
        const d = Math.hypot(imp.x - u.x, imp.y - (u.y - 16));
        if (d < bestProx) bestProx = d;
      }
    }
    if (bestProx < 220) {
      score += Math.min(90, sim.totalCarved * 0.45) + Math.max(0, (220 - bestProx) * 0.25);
    }
  }

  // Anti-stall penalty if impact repeats a stalled spot with no object progress
  if (score < 35 && stalledPoints.length > 0) {
    for (const imp of sim.impacts) {
      for (const sp of stalledPoints) {
        if (Math.hypot(imp.x - sp.x, imp.y - sp.y) < 22) {
          score -= 420;
        }
      }
    }
  }

  return score;
}

// Build tactical aim points for all remaining objectives
function buildAimPoints(obs, t, recycleObs, config) {
  const { hole, objects } = obs;
  const needTokens = hole.goal === 'tokens' || hole.goal === 'both';
  const needBug = hole.goal === 'bug' || hole.goal === 'both';
  const aims = [];

  const activeTokens = needTokens
    ? objects.filter(o => isTokenType(o.type) && isObjectActive(o))
    : [];

  for (const tok of activeTokens) {
    const sh = getObjectShape(tok, config);
    if (tok.type === 'pushToken') {
      // Aim top-center (zero horizontal impulse -> burst lands all 3 hits cleanly without pushing into pits!)
      aims.push({ x: sh.cx, y: sh.y0 + 3, kinds: ['burst', 'single'], tag: 'pushTop' });
      aims.push({ x: sh.cx, y: sh.cy, kinds: ['single', 'burst'], tag: 'pushCenter' });
      // Also test side blasts in case knocking pushToken off a ledge/map edge breaks it in 1 shot!
      aims.push({ x: sh.x0 - 10, y: sh.y1 - 4, kinds: ['single'], tag: 'pushRight' });
      aims.push({ x: sh.x1 + 10, y: sh.y1 - 4, kinds: ['single'], tag: 'pushLeft' });
    } else if (tok.type === 'bigToken') {
      const hp = tok.hp ?? 240;
      const prefKinds = hp <= 195 && hp > 120 ? ['burst', 'single', 'spread'] : ['burst', 'single'];
      aims.push({ x: sh.cx, y: sh.cy, kinds: prefKinds, tag: 'bigCenter' });
      aims.push({ x: sh.cx, y: sh.y0 + 6, kinds: prefKinds, tag: 'bigTop' });
      aims.push({ x: sh.x0 + 4, y: sh.cy, kinds: prefKinds, tag: 'bigLeft' });
      aims.push({ x: sh.x1 - 4, y: sh.cy, kinds: prefKinds, tag: 'bigRight' });
      // Check if undermining support can drop bigToken into void
      if (checkSupportBelow(t, sh.cx, sh.y1 + 44, 18) == null) {
        aims.push({ x: sh.cx, y: sh.y1 + 8, kinds: ['dig', 'single'], tag: 'undermine' });
      }
    } else {
      // Normal 1-HP token
      aims.push({ x: sh.cx, y: sh.cy, kinds: ['single', 'spread', 'burst'], tag: 'token' });
    }
  }

  // Pairwise midpoints between nearby tokens for 1-stroke multi-kills!
  for (let i = 0; i < activeTokens.length; i++) {
    for (let j = i + 1; j < activeTokens.length; j++) {
      const a = getObjectShape(activeTokens[i], config);
      const b = getObjectShape(activeTokens[j], config);
      const dist = Math.hypot(a.cx - b.cx, a.cy - b.cy);
      if (dist <= 64) {
        aims.push({
          x: (a.cx + b.cx) * 0.5,
          y: (a.cy + b.cy) * 0.5,
          kinds: ['single', 'spread', 'burst'],
          tag: 'multiToken',
        });
      } else if (dist <= 160) {
        // Spread midpoint can hit two wider-spaced tokens!
        aims.push({
          x: (a.cx + b.cx) * 0.5,
          y: (a.cy + b.cy) * 0.5,
          kinds: ['spread'],
          tag: 'spreadPair',
        });
      }
    }
  }

  // Bug push aim points & Recycle excavation aim points
  if (needBug) {
    const bug = objects.find(o => o.type === 'bug' && isObjectActive(o));
    if (bug && !bug.inRecycle && hole.recycle) {
      const rx = (hole.recycle.x0 + hole.recycle.x1) * 0.5;
      const pushDir = rx >= bug.x ? 1 : -1; // +1 = push right (hit left of bug), -1 = push left (hit right of bug)
      const r = bug.radius ?? 14;
      // Sample verified effective standoff distances behind bug (strictly inside R=52 cutoff; single/dig only!)
      for (const standoff of [39, 34, 29, 24, 19, 14, 10]) {
        const ax = bug.x - pushDir * standoff;
        const surfY = findColumnTopY(t, ax, Math.max(0, bug.y - 28), Math.min(896, bug.y + 28));
        if (surfY != null) {
          aims.push({
            x: ax,
            y: surfY - 1,
            kinds: ['single', 'dig'],
            tag: `bugPush_${standoff}`,
          });
        }
      }
      // Direct hit on lower push-side flank of Bug circle (avoids top-center downward crater sink)
      aims.push({
        x: bug.x - pushDir * (r - 1),
        y: bug.y - r * 0.55,
        kinds: ['single'],
        tag: 'bugFlank',
      });

      // If recycle is covered by terrain, add dig/burst targets on the recycle cover!
      if (recycleObs.count > 4 && recycleObs.digTarget) {
        aims.push({
          x: recycleObs.digTarget.x,
          y: recycleObs.digTarget.y,
          kinds: ['dig', 'burst', 'single'],
          tag: 'digRecycle',
        });
        // Entrance lip of recycle on Bug's side
        const lipX = pushDir > 0 ? hole.recycle.x0 + 16 : hole.recycle.x1 - 16;
        const lipY = findColumnTopY(t, lipX, Math.max(0, hole.recycle.y0 - 40), hole.recycle.y1 + 20) ?? hole.recycle.y0;
        aims.push({
          x: lipX,
          y: lipY,
          kinds: ['dig', 'burst'],
          tag: 'digRecycleLip',
        });
      }
    }
  }

  return aims;
}

// Evaluate paper airplane (fly) option using true 1-step simulated lookahead
function chooseBestFlyAction(obs, t, aims, recycleObs, currentBestScore, config, cal, t0, hardDeadlineMs) {
  if (!obs.fly || obs.fly <= 0) return null;
  if (currentBestScore >= 420) return null;

  const needTokens = obs.hole.goal === 'tokens' || obs.hole.goal === 'both';
  const needBug = obs.hole.goal === 'bug' || obs.hole.goal === 'both';
  const unfinishedObjs = obs.objects.filter(o => {
    if (!isObjectActive(o)) return false;
    if (needTokens && isTokenType(o.type)) return true;
    if (needBug && o.type === 'bug' && !o.inRecycle) return true;
    return false;
  });
  if (unfinishedObjs.length === 0) return null;

  const meanTargetX = unfinishedObjs.reduce((s, o) => s + o.x, 0) / unfinishedObjs.length;
  const curDistToTargets = Math.abs(obs.actor.x - meanTargetX);

  const muzzle = getActorMuzzle(obs.actor, config);
  const W = config?.W ?? 1600;
  const rawSpots = [];

  const prefDir = meanTargetX >= obs.actor.x ? 1 : -1;
  for (const dir of [prefDir, -prefDir]) {
    for (let angle = 18; angle <= 78; angle += 12) {
      for (let power = 28; power <= 94; power += 11) {
        const imp = simulateProjectile({
          startX: muzzle.x,
          startY: muzzle.y,
          actualAngleDeg: angle,
          power,
          dir,
          wind: obs.hole.wind || 0,
          terrain: t,
          objects: null,
          config,
          ignoreObjects: true,
          substepFactor: 5,
        });
        if (imp.hit !== 'ground') continue;
        if (imp.x < 40 || imp.x > W - 40) continue;
        if (Math.abs(imp.x - obs.actor.x) < 120) continue;

        const rawFy = findColumnTopY(t, imp.x, Math.max(0, imp.y - 20), Math.min(892, imp.y + 20));
        if (rawFy == null || !hasHeadroom(t, imp.x, rawFy, config)) continue;

        let badSpot = false;
        for (const o of obs.objects) {
          if (o.type === 'chicken' && isObjectActive(o) && Math.hypot(o.x - imp.x, o.y - rawFy) < 48) {
            badSpot = true;
            break;
          }
        }
        if (badSpot) continue;

        if (rawSpots.some(s => Math.abs(s.x - imp.x) < 65 && Math.abs(s.rawFy - rawFy) < 24)) continue;

        const distToTargets = Math.abs(imp.x - meanTargetX);
        const preRank = (curDistToTargets - distToTargets) + (780 - rawFy) * 0.4;
        rawSpots.push({ angle, power, dir, x: imp.x, rawFy, preRank });
      }
    }
  }

  rawSpots.sort((a, b) => b.preRank - a.preRank);
  const topSpots = rawSpots.slice(0, 6);
  const primaryAims = aims.filter(a => !a.tag.startsWith('digRecycle')).slice(0, 6);
  const evalAims = primaryAims.length > 0 ? primaryAims : aims.slice(0, 4);

  let bestFly = null;
  let bestNextScore = Math.max(520, currentBestScore + 280);

  for (const sp of topSpots) {
    if (performance.now() - t0 > hardDeadlineMs) break;
    const exactImp = simulateProjectile({
      startX: muzzle.x,
      startY: muzzle.y,
      actualAngleDeg: sp.angle,
      power: sp.power,
      dir: sp.dir,
      wind: obs.hole.wind || 0,
      terrain: t,
      objects: null,
      config,
      ignoreObjects: true,
      substepFactor: 1,
    });
    if (exactImp.hit !== 'ground' || exactImp.x < 40 || exactImp.x > W - 40) continue;
    const rawFy = findColumnTopY(t, exactImp.x, Math.max(0, exactImp.y - 20), Math.min(892, exactImp.y + 20));
    if (rawFy == null || !hasHeadroom(t, exactImp.x, rawFy, config)) continue;

    const fy = estimateFootY(t, exactImp.x, rawFy);
    const gAng = estimateGroundAngle(t, exactImp.x, rawFy, config);
    const newStand = {
      x: exactImp.x,
      footY: fy,
      groundAngle: gAng,
      muzzle: getMuzzlePosition(exactImp.x, fy, gAng, config),
      isCurrent: false,
    };

    let spotBest = -Infinity;
    for (const aim of evalAims) {
      const d = aim.x >= newStand.muzzle.x ? 1 : -1;
      for (const testAng of [24, 46, 66]) {
        const sol = solvePowerForAngle(newStand, testAng, d, aim.x, aim.y, obs.hole.wind || 0, t, obs.objects, config);
        if (!sol || sol.dist > 36) continue;
        const kind = aim.kinds[0] || 'single';
        const atk = { kind, angle: sol.angle, power: sol.power, dir: d };
        const sim = simulateFullTurn(newStand, atk, obs.hole, t, obs.objects, config, cal);
        const sc = scoreSimulatedTurn(sim, newStand, atk, obs, t, recycleObs, [], config);
        if (sc > spotBest) spotBest = sc;
      }
    }

    const adjusted = spotBest - Math.abs(exactImp.x - meanTargetX) * 0.05;
    if (adjusted > bestNextScore) {
      bestNextScore = adjusted;
      bestFly = { fly: { angle: sp.angle, power: sp.power, dir: sp.dir } };
    }
  }
  return bestFly;
}

export function decideTurnAction(obs, state, config, cal) {
  const t0 = performance.now();
  const hardLimitMs = Math.min(620, (obs.deadlineMs || 1000) * 0.65);
  const maxBudgetMs = Math.min(380, (obs.deadlineMs || 1000) * 0.40);

  const terrain = decodeTerrain(obs.terrain);
  const stands = getStandCandidates(terrain, obs.hole, obs.actor, config, 28);
  const bugObj = obs.objects.find(o => o.type === 'bug' && isObjectActive(o));
  const recycleObs = countRecycleObstruction(terrain, obs.hole.recycle, bugObj?.x ?? 800);
  const aims = buildAimPoints(obs, terrain, recycleObs, config);

  const candidateAngles = [8, 15, 22, 28, 34, 40, 46, 52, 58, 64, 70, 76, 82];
  let bestChoice = null;
  let bestScore = -Infinity;

  // Deduplicate similar (stand.x, kind, dir, round(angle), round(power)) before expensive full sim
  const seenKeys = new Set();

  outer: for (let sIdx = 0; sIdx < stands.length; sIdx++) {
    const stand = stands[sIdx];
    for (let aIdx = 0; aIdx < aims.length; aIdx++) {
      if (performance.now() - t0 > maxBudgetMs) break outer;
      const aim = aims[aIdx];

      // Determine plausible firing directions (usually toward aim, plus both dirs in strong wind or high angle)
      const primaryDir = aim.x >= stand.muzzle.x ? 1 : -1;
      const dirs = Math.abs(aim.x - stand.muzzle.x) < 90 || Math.abs(obs.hole.wind || 0) >= 5
        ? [primaryDir, -primaryDir]
        : [primaryDir];

      for (const dir of dirs) {
        for (const angle of candidateAngles) {
          if (performance.now() - t0 > maxBudgetMs) break outer;
          const sol = solvePowerForAngle(
            stand,
            angle,
            dir,
            aim.x,
            aim.y,
            obs.hole.wind || 0,
            terrain,
            obs.objects,
            config
          );
          if (!sol) continue;

          // Allow larger distance if blocked by wall (for dig/burst tunneling!), otherwise keep close hits
          const kindsToTry = sol.dist <= 38
            ? aim.kinds
            : sol.dist <= 130
              ? ['dig', 'burst']
              : null;
          if (!kindsToTry) continue;

          for (const kind of kindsToTry) {
            const key = `${stand.x}_${kind}_${dir}_${Math.round(sol.angle * 2)}_${Math.round(sol.power * 2)}`;
            if (seenKeys.has(key)) continue;
            seenKeys.add(key);

            const attack = {
              kind,
              angle: Math.max(0, Math.min(85, sol.angle)),
              power: Math.max(1, Math.min(100, sol.power)),
              dir,
            };
            const sim = simulateFullTurn(stand, attack, obs.hole, terrain, obs.objects, config, cal);
            const sc = scoreSimulatedTurn(
              sim,
              stand,
              attack,
              obs,
              terrain,
              recycleObs,
              state.stalledPoints,
              config
            );

            if (sc > bestScore) {
              bestScore = sc;
              bestChoice = { stand, attack, sim, score: sc, aimTag: aim.tag };
            }
          }
        }
      }
    }
  }

  // Fine-tune around the winning (stand, attack) if time permits!
  if (bestChoice && performance.now() - t0 < maxBudgetMs + 90) {
    const { stand, attack } = bestChoice;
    fineLoop: for (const dAng of [-3, -1, 0, 1, 3]) {
      for (const dPow of [-2, -0.6, 0, 0.6, 2]) {
        if (performance.now() - t0 > maxBudgetMs + 100) break fineLoop;
        if (dAng === 0 && dPow === 0) continue;
        const ang = Math.round(Math.max(0, Math.min(85, attack.angle + dAng)) * 100) / 100;
        const pow = Math.round(Math.max(1, Math.min(100, attack.power + dPow)) * 100) / 100;
        const candAtk = { kind: attack.kind, angle: ang, power: pow, dir: attack.dir };
        const sim = simulateFullTurn(stand, candAtk, obs.hole, terrain, obs.objects, config, cal);
        const sc = scoreSimulatedTurn(sim, stand, candAtk, obs, terrain, recycleObs, state.stalledPoints, config);
        if (sc > bestScore) {
          bestScore = sc;
          bestChoice = { stand, attack: candAtk, sim, score: sc, aimTag: bestChoice.aimTag + '+fine' };
        }
      }
    }
  }

  // Consider paper airplane if direct shots are completely blocked/low-value
  if (performance.now() - t0 < hardLimitMs - 60) {
    const flyCmd = chooseBestFlyAction(obs, terrain, aims, recycleObs, bestScore, config, cal, t0, hardLimitMs);
    if (flyCmd) {
      return { action: flyCmd, debug: { mode: 'fly', bestDirectScore: +bestScore.toFixed(1), ms: +(performance.now() - t0).toFixed(1) } };
    }
  }

  // Consider unplayable ONLY if goal needs bug, bug is stuck in a deep pit far from initial Bug pos,
  // and consecutive bug turns produced zero movement while initial Bug pos would be much better.
  if (
    bestScore < 15 &&
    state.consecutiveStalls >= 2 &&
    bugObj &&
    !bugObj.inRecycle &&
    state.initialBug &&
    bugObj.y - state.initialBug.y > 45 &&
    Math.abs(bugObj.x - state.initialBug.x) > 30
  ) {
    state.consecutiveStalls = 0;
    return { action: { unplayable: true }, debug: { mode: 'unplayable_reset_bug', ms: +(performance.now() - t0).toFixed(1) } };
  }

  // Fallback safe legal shot if no aim succeeded (never return invalid command)
  if (!bestChoice) {
    const curStand = stands[0];
    const targetX = aims[0]?.x ?? 800;
    const dir = targetX >= curStand.x ? 1 : -1;
    return {
      action: { attack: { kind: 'dig', angle: 45, power: 60, dir } },
      debug: { mode: 'fallback', ms: +(performance.now() - t0).toFixed(1) },
    };
  }

  const safeAtk = {
    kind: bestChoice.attack.kind,
    angle: Math.max(0, Math.min(85, Number(bestChoice.attack.angle) || 45)),
    power: Math.max(1, Math.min(100, Number(bestChoice.attack.power) || 60)),
    dir: bestChoice.attack.dir === -1 ? -1 : 1,
  };
  const reply = { attack: safeAtk };
  if (obs.hole.teeActive && obs.hole.tee && Math.abs(bestChoice.stand.x - obs.actor.x) >= 2) {
    const mxMin = Math.ceil(obs.hole.tee.x0);
    const mxMax = Math.floor(obs.hole.tee.x1);
    if (mxMin <= mxMax) {
      reply.moveX = Math.max(mxMin, Math.min(mxMax, Math.round(bestChoice.stand.x)));
    }
  }

  return {
    action: reply,
    predictedImpacts: bestChoice.sim.impacts,
    debug: {
      mode: bestChoice.aimTag,
      score: +bestChoice.score.toFixed(1),
      standX: bestChoice.stand.x,
      predImp: bestChoice.sim.impacts.map(i => `(${i.x.toFixed(1)},${i.y.toFixed(1)},${i.hit})`).join(','),
      ms: +(performance.now() - t0).toFixed(1),
    },
  };
}
