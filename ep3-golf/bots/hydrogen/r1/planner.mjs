import {
  carveCircle,
  decodeTerrain,
  distPointToBox,
  distPointToCenter,
  findSurfaceAtX,
  getMuzzle,
  getObjectBox,
  getStandPose,
  hasLineOfSight,
  hitTestObject,
  isObjectActive,
  isSolidPixel,
  simulateAttack,
  simulateProjectile,
} from './physics.mjs';

function sampleStandPoses(ter, obs) {
  const { actor, hole, config } = obs;
  const poses = [];
  const curFeetY = actor.y + config.BODY.footOffset;
  poses.push({
    x: actor.x,
    feetY: curFeetY,
    groundAngle: actor.groundAngle,
    isCurrent: true,
  });

  if (!hole.teeActive || !hole.tee) return poses;

  const { x0, x1 } = hole.tee;
  const minX = Math.max(x0, Math.min(x1, Math.ceil(x0 + 2)));
  const maxX = Math.max(x0, Math.min(x1, Math.floor(x1 - 2)));
  if (maxX < minX) return poses;

  const span = maxX - minX;
  const step = Math.max(18, Math.floor(span / 8));
  const xs = new Set([minX, maxX, Math.round((minX + maxX) / 2)]);
  for (let x = minX; x <= maxX; x += step) xs.add(x);

  for (const rawX of xs) {
    const x = Math.max(x0, Math.min(x1, rawX));
    if (Math.abs(x - actor.x) < 6) continue;
    const pose = getStandPose(ter, x, hole.tee, config.BODY);
    if (pose) poses.push({ ...pose, isCurrent: false });
    if (poses.length >= 9) break;
  }
  return poses;
}

function countSolidInRect(ter, x0, y0, x1, y1) {
  const gx0 = Math.max(0, Math.floor(x0 / ter.cell));
  const gx1 = Math.min(ter.width - 1, Math.floor(x1 / ter.cell));
  const gy0 = Math.max(0, Math.floor(y0 / ter.cell));
  const gy1 = Math.min(ter.height - 1, Math.floor(y1 / ter.cell));
  let count = 0;
  for (let gy = gy0; gy <= gy1; gy++) {
    const row = gy * ter.width;
    for (let gx = gx0; gx <= gx1; gx++) {
      if (ter.grid[row + gx] === 1) count++;
    }
  }
  return count;
}

function countCarvedInRect(ter, cx, cy, r, rect) {
  const x0 = Math.max(rect.x0, cx - r);
  const x1 = Math.min(rect.x1, cx + r);
  const y0 = Math.max(rect.y0, cy - r);
  const y1 = Math.min(rect.y1, cy + r);
  if (x1 <= x0 || y1 <= y0) return 0;
  const gx0 = Math.max(0, Math.floor(x0 / ter.cell));
  const gx1 = Math.min(ter.width - 1, Math.floor(x1 / ter.cell));
  const gy0 = Math.max(0, Math.floor(y0 / ter.cell));
  const gy1 = Math.min(ter.height - 1, Math.floor(y1 / ter.cell));
  const r2 = r * r;
  let count = 0;
  for (let gy = gy0; gy <= gy1; gy++) {
    const py = (gy + 0.5) * ter.cell;
    const dy = py - cy;
    const row = gy * ter.width;
    for (let gx = gx0; gx <= gx1; gx++) {
      if (ter.grid[row + gx] !== 1) continue;
      const px = (gx + 0.5) * ter.cell;
      const dx = px - cx;
      if (dx * dx + dy * dy <= r2) count++;
    }
  }
  return count;
}

function findBlockingPathZone(ter, bugObj, rc) {
  const recCenterX = (rc.x0 + rc.x1) / 2;
  const dir = Math.sign(recCenterX - bugObj.x) || 1;
  const entryX = dir > 0 ? rc.x0 : rc.x1;
  const probeStart = bugObj.x + dir * 20;
  const probeEnd = dir > 0 ? Math.min(rc.x1 - 24, entryX + 80) : Math.max(rc.x0 + 24, entryX - 80);
  const minX = Math.min(probeStart, probeEnd);
  const maxX = Math.max(probeStart, probeEnd);
  const yTop = Math.max(0, bugObj.y - 36);
  const yBot = Math.min(ter.height * ter.cell - 1, bugObj.y - 8);

  if (maxX > minX && yBot > yTop) {
    const solidPath = countSolidInRect(ter, minX, yTop, maxX, yBot);
    if (solidPath > 12) {
      return { x0: minX, y0: yTop - 16, x1: maxX, y1: bugObj.y - 2 };
    }
  }

  const floorY = findSurfaceAtX(ter, recCenterX, rc.y0, 28) ?? Math.min(rc.y1, bugObj.y);
  const zoneBot = Math.min(rc.y1, Math.max(bugObj.y, floorY) - 4);
  if (zoneBot > rc.y0 + 16) {
    const solidCount = countSolidInRect(ter, rc.x0, rc.y0, rc.x1, zoneBot);
    let hasOpenPocket = false;
    for (let x = rc.x0 + 24; x <= rc.x1 - 24; x += 16) {
      const surf = findSurfaceAtX(ter, x, rc.y0, 30);
      if (surf != null && surf >= rc.y0 + 28 && surf <= rc.y1) {
        hasOpenPocket = true;
        break;
      }
    }
    if (solidCount > 20 && !hasOpenPocket) {
      return { x0: rc.x0, y0: rc.y0, x1: rc.x1, y1: zoneBot };
    }
  }
  return null;
}

function precomputeContext(ter, obs) {
  const { hole, objects, config } = obs;
  const needTokens = hole.goal === 'tokens' || hole.goal === 'both';
  const needBug = hole.goal === 'bug' || hole.goal === 'both';

  const chickens = [];
  const tokenTargets = [];
  let bug = null;

  for (const o of objects) {
    if (o.type === 'chicken' && isObjectActive(o)) {
      chickens.push({ obj: o, box: getObjectBox(o, config) });
    } else if (
      needTokens &&
      isObjectActive(o) &&
      (o.type === 'token' || o.type === 'bigToken' || o.type === 'pushToken')
    ) {
      tokenTargets.push({ obj: o, box: getObjectBox(o, config) });
    } else if (needBug && o.type === 'bug' && !o.inRecycle && !o.removed) {
      bug = { obj: o, box: getObjectBox(o, config) };
    }
  }

  let recClearZone = null;
  let recCenter = null;
  if (bug && hole.recycle) {
    const rc = hole.recycle;
    recCenter = { x: (rc.x0 + rc.x1) / 2, y: (rc.y0 + rc.y1) / 2 };
    recClearZone = findBlockingPathZone(ter, bug.obj, rc);
  }

  const scratchTer = {
    width: ter.width,
    height: ter.height,
    cell: ter.cell,
    grid: new Uint8Array(ter.grid.length),
  };

  const scratchTer2 = {
    width: ter.width,
    height: ter.height,
    cell: ter.cell,
    grid: new Uint8Array(ter.grid.length),
  };

  return { needTokens, needBug, chickens, tokenTargets, bug, recClearZone, recCenter, scratchTer, scratchTer2 };
}

function chickenDangerPenalty(ter, chickens, imp, atk) {
  let pen = 0;
  for (let i = 0; i < chickens.length; i++) {
    const ch = chickens[i];
    if (imp.objectId === ch.obj.id) return -1e7;
    const dBox = distPointToBox(imp.x, imp.y, ch.box);
    if (dBox < atk.damageRadius + 6) {
      if (dBox < 6 || hasLineOfSight(ter, imp.x, imp.y, ch.box.cx, ch.box.cy)) {
        if (dBox < atk.damageRadius + 1) return -1e7;
        pen = Math.min(pen, -5000);
      }
    }
    if (
      Math.abs(imp.x - ch.obj.x) < atk.radius + 10 &&
      imp.y >= ch.obj.y - 6 &&
      imp.y <= ch.obj.y + atk.radius + 8
    ) {
      const below = findSurfaceAtX(ter, ch.obj.x, ch.obj.y + atk.radius + 10, 10);
      if (below == null || below > ch.obj.y + 120) return -5e5;
    }
  }
  return pen;
}

function predictBugSlide(ter, bugObj, imp, pushDir, config) {
  const dx = bugObj.x - imp.x;
  const dy = bugObj.y - imp.y;
  const d = Math.hypot(dx, dy);
  const R = 70.6;
  if (d >= R) return null;

  // Hits above bug's center push downward into ground rather than hopping cleanly
  const cos = Math.abs(dx) / Math.max(1, d);
  const cosFactor = imp.y < bugObj.y - 10 ? cos * cos : imp.y < bugObj.y - 5 ? cos : 1;
  let slide = Math.pow(0.45 * (R - d), 2) * cosFactor;

  const aheadSurf = findSurfaceAtX(ter, bugObj.x + pushDir * Math.min(slide, 180), bugObj.y - 20, 28);
  if (aheadSurf != null && aheadSurf > bugObj.y + 8) {
    slide += 0.5 * (aheadSurf - bugObj.y);
  }

  let blockedAt = null;
  if (aheadSurf == null || aheadSurf <= bugObj.y + 8) {
    const checkDist = Math.min(slide, 400);
    for (let s = 20; s <= checkDist; s += 16) {
      const px = bugObj.x + pushDir * s;
      if (px < 0 || px >= config.W) break;
      if (isSolidPixel(ter, px, bugObj.y - 22)) {
        blockedAt = px - pushDir * 14;
        break;
      }
    }
  }

  const landX = blockedAt != null ? blockedAt : bugObj.x + pushDir * slide;
  return { landX, slide, d, blocked: blockedAt != null };
}

function evaluateShot(ter, obs, ctx, pose, kind, angle, power, dir, impacts, history) {
  const { hole, config } = obs;
  const atk = config.ATTACKS[kind];
  const { chickens, tokenTargets, bug, recClearZone, recCenter, scratchTer } = ctx;

  const isMulti = impacts.length > 1;
  let evalTer = ter;
  if (isMulti) {
    scratchTer.grid.set(ter.grid);
    evalTer = scratchTer;
  }

  const hpLeft = new Array(tokenTargets.length);
  const pushed = new Array(tokenTargets.length);
  for (let i = 0; i < tokenTargets.length; i++) {
    hpLeft[i] = tokenTargets[i].obj.hp ?? 1;
    pushed[i] = false;
  }

  let score = 0;
  let hitsUsefulTarget = false;
  let bugPushBest = null;
  let minTargetDist = Infinity;

  for (let s = 0; s < impacts.length; s++) {
    const imp = impacts[s];
    if (imp.hit === 'oob') {
      score -= 80;
      continue;
    }

    // Never blow up the ground right under our own feet
    if (Math.hypot(imp.x - pose.x, imp.y - pose.feetY) < atk.radius + 6) {
      return -1e5;
    }

    const chPen = chickenDangerPenalty(evalTer, chickens, imp, atk);
    if (chPen <= -1e6) return chPen;
    score += chPen;

    for (let i = 0; i < tokenTargets.length; i++) {
      if (hpLeft[i] <= 0) continue;
      const t = tokenTargets[i];
      const dCenter = distPointToCenter(imp.x, imp.y, t.box);
      if (dCenter < minTargetDist) minTargetDist = dCenter;

      const direct = imp.objectId === t.obj.id || hitTestObject(t.obj, imp.x, imp.y, config);
      const dBox = distPointToBox(imp.x, imp.y, t.box);

      if (direct || dBox < atk.damageRadius) {
        if (!direct && !hasLineOfSight(evalTer, imp.x, imp.y, t.box.cx, t.box.cy)) continue;
        const dmg = direct ? atk.damage : atk.damage * (1 - dBox / atk.damageRadius);

        hitsUsefulTarget = true;
        if (t.obj.type === 'token') {
          hpLeft[i] = 0;
          score += 1200;
        } else if (t.obj.type === 'bigToken') {
          const prev = hpLeft[i];
          const dealt = Math.min(prev, dmg);
          hpLeft[i] = prev - dmg;
          if (hpLeft[i] <= 0) {
            score += 1500 + dealt * 2;
          } else {
            score += dealt * 5.5;
          }
        } else if (t.obj.type === 'pushToken') {
          if (kind === 'burst' && s > 0 && pushed[i]) continue;
          const prev = hpLeft[i];
          const dealt = Math.min(prev, dmg);
          hpLeft[i] = prev - dmg;
          pushed[i] = true;
          if (hpLeft[i] <= 0) {
            score += 1450 + dealt * 2;
          } else {
            score += dealt * 5.0;
            const pushDir = Math.sign(t.obj.x - imp.x) || 1;
            const landX = t.obj.x + pushDir * 90;
            if (landX < 10 || landX > config.W - 10) {
              score += 250;
            } else {
              const landCheck = findSurfaceAtX(evalTer, landX, t.obj.y - 30, 30);
              if (landCheck == null) score += 220;
              else if (landCheck > t.obj.y + 80) score -= 220;
              if (!hasLineOfSight(evalTer, pose.x, pose.feetY - 35, landX, (landCheck ?? t.obj.y) - 15)) {
                score -= 180;
              }
            }
          }
        }
      }
    }

    if (bug && recCenter) {
      const rc = hole.recycle;
      const dxToRec = recCenter.x - bug.obj.x;
      const pushDir = Math.sign(dxToRec) || 1;
      const dBug = distPointToCenter(imp.x, imp.y, bug.box);
      if (dBug < minTargetDist) minTargetDist = dBug;

      if (recClearZone) {
        const carved = countCarvedInRect(evalTer, imp.x, imp.y, atk.radius, recClearZone);
        if (carved > 4) {
          score += Math.min(600, carved * 12) + (kind === 'dig' ? 140 : 0);
          hitsUsefulTarget = true;
        }
      }

      if (dBug < 75 && hasLineOfSight(evalTer, imp.x, imp.y, bug.box.cx, bug.box.cy)) {
        const deltaX = bug.obj.x - imp.x;
        if (deltaX * pushDir > 3 && dBug > 8) {
          const pred = predictBugSlide(evalTer, bug.obj, imp, pushDir, config);
          if (pred) {
            hitsUsefulTarget = true;
            let pushScore = 600;
            const lx = pred.landX;
            const margin = 18;
            if (lx >= rc.x0 + margin && lx <= rc.x1 - margin) {
              const distFromCenter = Math.abs(lx - recCenter.x);
              pushScore = 1350 - distFromCenter * 0.6;
            } else if (lx < 20 || lx > config.W - 20) {
              pushScore = -1500;
            } else {
              const remDist = Math.abs(lx - recCenter.x);
              const curDist = Math.abs(bug.obj.x - recCenter.x);
              if (remDist < curDist) {
                pushScore = 650 + Math.max(0, (curDist - remDist) * 1.2);
              } else {
                pushScore = 400 - (remDist - curDist) * 0.8;
              }
            }

            if (kind === 'single') pushScore += 120;
            else if (kind === 'dig') pushScore += 40;
            else if (kind === 'burst') pushScore -= 250;
            else if (kind === 'spread') pushScore -= 220;

            if (bugPushBest == null || pushScore > bugPushBest) {
              bugPushBest = pushScore;
            }
          }
        } else if (deltaX * pushDir < -2) {
          score -= 700;
        }
      }
    }

    if (isMulti && s < impacts.length - 1) {
      carveCircle(evalTer, imp.x, imp.y, atk.radius);
    }
  }

  if (bugPushBest != null) score += bugPushBest;

  if (!hitsUsefulTarget && minTargetDist < Infinity) {
    const firstImp = impacts[0];
    if (firstImp && firstImp.hit === 'terrain') {
      score += Math.max(-900, 240 - minTargetDist * 0.3);
      if (kind === 'dig') score += 40;
      if (kind === 'burst') score += 20;
    }
  }

  if (kind === 'single') score += 5;

  // Impact-based anti-repeat: only penalize if predicted impact lands on the same spot as an unproductive prior shot
  if (history && history.length > 0 && impacts[0]) {
    const imp0 = impacts[0];
    for (const h of history) {
      if (!h.madeProgress && h.actualImpacts && h.actualImpacts[0]) {
        const oldImp = h.actualImpacts[0];
        const sameSpot = Math.hypot(imp0.x - oldImp.x, imp0.y - oldImp.y) < 12;
        if (sameSpot && h.kind === kind) {
          score -= 600;
        }
      }
    }
  }

  return score;
}

function evaluateBreachValue(ter, obs, ctx, poses, breachImpacts, craterRadius) {
  const { objects, config, hole } = obs;
  const wind = hole.wind ?? 0;
  const workTer = ctx.scratchTer2;
  workTer.grid.set(ter.grid);
  for (const imp of breachImpacts) {
    if (imp.hit !== 'oob') carveCircle(workTer, imp.x, imp.y, craterRadius);
  }

  let bestFollowup = 0;
  const allTargets = [...ctx.tokenTargets, ...(ctx.bug ? [ctx.bug] : [])];
  if (allTargets.length === 0) return 0;

  for (let pi = 0; pi < Math.min(poses.length, 3); pi++) {
    const pose = poses[pi];
    const { mx, my } = getMuzzle(pose.x, pose.feetY, pose.groundAngle, config.BODY.muzzleHeight);
    for (const dir of [1, -1]) {
      for (let angle = 10; angle <= 80; angle += 10) {
        const actualAngle = angle + dir * pose.groundAngle;
        for (let power = 20; power <= 100; power += 10) {
          const imp = simulateProjectile(workTer, objects, config, mx, my, actualAngle, power, dir, wind, { dtScale: 10 });
          if (imp.hit === 'oob') continue;
          for (const t of allTargets) {
            if (imp.objectId === t.obj.id || distPointToBox(imp.x, imp.y, t.box) < 30) {
              return 480;
            }
            const d = distPointToCenter(imp.x, imp.y, t.box);
            const bonus = Math.max(0, 320 - d * 1.2);
            if (bonus > bestFollowup) bestFollowup = bonus;
          }
        }
      }
    }
  }
  return bestFollowup;
}

export function planTurn(obs, history, initBugPos = null) {
  const tStart = Date.now();
  const { hole, objects, config, terrain, fly } = obs;
  const ter = decodeTerrain(terrain);
  const ctx = precomputeContext(ter, obs);
  const poses = sampleStandPoses(ter, obs);
  const wind = hole.wind ?? 0;

  const coarseCandidates = [];
  for (let pi = 0; pi < poses.length; pi++) {
    const pose = poses[pi];
    const { mx, my } = getMuzzle(pose.x, pose.feetY, pose.groundAngle, config.BODY.muzzleHeight);
    for (const dir of [1, -1]) {
      for (let angle = 0; angle <= 85; angle += 5) {
        const actualAngle = angle + dir * pose.groundAngle;
        for (let power = 5; power <= 100; power += 5) {
          const imp = simulateProjectile(ter, objects, config, mx, my, actualAngle, power, dir, wind, { dtScale: 8 });
          if (imp.hit === 'oob' || imp.steps < 3) continue;
          const sc = evaluateShot(ter, obs, ctx, pose, 'single', angle, power, dir, [imp], history);
          coarseCandidates.push({ pi, pose, dir, angle, power, sc, imp });
        }
      }
    }
  }

  coarseCandidates.sort((a, b) => b.sc - a.sc);

  const seeds = [];
  const addSeed = c => {
    if (!c) return;
    const tooClose = seeds.some(
      s => s.pi === c.pi && s.dir === c.dir && Math.abs(s.angle - c.angle) <= 6 && Math.abs(s.power - c.power) <= 6
    );
    if (!tooClose) seeds.push(c);
  };

  for (const c of coarseCandidates) {
    if (seeds.length >= 12) break;
    addSeed(c);
  }

  const allTargets = [...ctx.tokenTargets, ...(ctx.bug ? [ctx.bug] : [])];
  for (const t of allTargets) {
    let bestNear = null;
    let bestD = Infinity;
    for (const c of coarseCandidates) {
      const d = distPointToCenter(c.imp.x, c.imp.y, t.box);
      if (d < bestD) {
        bestD = d;
        bestNear = c;
      }
    }
    if (bestNear && seeds.length < 18) addSeed(bestNear);
  }

  const hasHighHpToken = ctx.tokenTargets.some(
    t => (t.obj.type === 'bigToken' || t.obj.type === 'pushToken') && (t.obj.hp ?? 150) > 120
  );
  const hasMultiToken = ctx.tokenTargets.length > 1;

  let best = null;
  for (const seed of seeds) {
    if (Date.now() - tStart > 420) break;
    const pose = seed.pose;
    for (let da = -4; da <= 4; da += 2) {
      const angle = seed.angle + da;
      if (angle < 0 || angle > 85) continue;
      for (let dp = -4; dp <= 4; dp += 2) {
        const power = seed.power + dp;
        if (power < 1 || power > 100) continue;

        const resSingle = simulateAttack(ter, objects, config, pose, 'single', angle, power, seed.dir, wind, 2);
        const scSingle = evaluateShot(ter, obs, ctx, pose, 'single', angle, power, seed.dir, resSingle.impacts, history);
        if (!best || scSingle > best.sc) {
          best = { sc: scSingle, pose, kind: 'single', angle, power, dir: seed.dir, impacts: resSingle.impacts };
        }

        const scDig = evaluateShot(ter, obs, ctx, pose, 'dig', angle, power, seed.dir, resSingle.impacts, history);
        if (scDig > best.sc) {
          best = { sc: scDig, pose, kind: 'dig', angle, power, dir: seed.dir, impacts: resSingle.impacts };
        }

        const firstImp = resSingle.impacts[0];
        const nearAnyTarget =
          firstImp &&
          firstImp.hit !== 'oob' &&
          allTargets.some(t => distPointToCenter(firstImp.x, firstImp.y, t.box) < 110);

        if (nearAnyTarget) {
          const hitBigOrWall =
            firstImp.hit === 'terrain' ||
            ctx.tokenTargets.some(t => t.obj.id === firstImp.objectId && t.obj.type !== 'pushToken');
          if (hitBigOrWall) {
            const resBurst = simulateAttack(ter, objects, config, pose, 'burst', angle, power, seed.dir, wind, 2);
            const scBurst = evaluateShot(ter, obs, ctx, pose, 'burst', angle, power, seed.dir, resBurst.impacts, history);
            if (scBurst > best.sc) {
              best = { sc: scBurst, pose, kind: 'burst', angle, power, dir: seed.dir, impacts: resBurst.impacts };
            }
          }

          if (hasHighHpToken || hasMultiToken) {
            const resSpread = simulateAttack(ter, objects, config, pose, 'spread', angle, power, seed.dir, wind, 2);
            const scSpread = evaluateShot(ter, obs, ctx, pose, 'spread', angle, power, seed.dir, resSpread.impacts, history);
            if (scSpread > best.sc) {
              best = { sc: scSpread, pose, kind: 'spread', angle, power, dir: seed.dir, impacts: resSpread.impacts };
            }
          }
        }
      }
    }
  }

  // 2-ply terrain breach lookahead if no direct useful shot scored >= 450
  if ((!best || best.sc < 450) && Date.now() - tStart < 550) {
    const breachPool = [];
    for (const seed of seeds.slice(0, 6)) {
      const resD = simulateAttack(ter, objects, config, seed.pose, 'single', seed.angle, seed.power, seed.dir, wind, 2);
      const baseDig = evaluateShot(ter, obs, ctx, seed.pose, 'dig', seed.angle, seed.power, seed.dir, resD.impacts, history);
      if (baseDig > -1000) {
        breachPool.push({ pose: seed.pose, kind: 'dig', angle: seed.angle, power: seed.power, dir: seed.dir, impacts: resD.impacts, baseSc: baseDig });
      }
      const resB = simulateAttack(ter, objects, config, seed.pose, 'burst', seed.angle, seed.power, seed.dir, wind, 2);
      const baseBurst = evaluateShot(ter, obs, ctx, seed.pose, 'burst', seed.angle, seed.power, seed.dir, resB.impacts, history);
      if (baseBurst > -1000) {
        breachPool.push({ pose: seed.pose, kind: 'burst', angle: seed.angle, power: seed.power, dir: seed.dir, impacts: resB.impacts, baseSc: baseBurst });
      }
    }
    for (const bp of breachPool) {
      if (Date.now() - tStart > 680) break;
      const bVal = evaluateBreachValue(ter, obs, ctx, poses, bp.impacts, config.ATTACKS[bp.kind].radius);
      const totalSc = bp.baseSc * 0.5 + bVal;
      if (!best || totalSc > best.sc) {
        best = { sc: totalSc, pose: bp.pose, kind: bp.kind, angle: bp.angle, power: bp.power, dir: bp.dir, impacts: bp.impacts };
      }
    }
  }

  // Exact dtScale=1 micro-polish around best
  if (best && Date.now() - tStart < 760) {
    const p0 = best.pose;
    const k0 = best.kind;
    for (let da = -2; da <= 2; da += 1) {
      const angle = Math.max(0, Math.min(85, best.angle + da));
      for (let dp = -2; dp <= 2; dp += 1) {
        const power = Math.max(1, Math.min(100, best.power + dp));
        const res = simulateAttack(ter, objects, config, p0, k0, angle, power, best.dir, wind, 1);
        const sc = evaluateShot(ter, obs, ctx, p0, k0, angle, power, best.dir, res.impacts, history);
        if (sc > best.sc) {
          best = { sc, pose: p0, kind: k0, angle, power, dir: best.dir, impacts: res.impacts };
        }
      }
    }
  }

  // Paper plane if no useful direct/breach shot and fly lands somewhere that can reach a target
  if (fly > 0 && hole.teeActive && (!best || best.sc < 380) && Date.now() - tStart < 850) {
    const pose = poses[0];
    const { mx, my } = getMuzzle(pose.x, pose.feetY, pose.groundAngle, config.BODY.muzzleHeight);
    const activeTargets = [...ctx.tokenTargets, ...(ctx.bug ? [ctx.bug] : [])];
    let bestFly = null;
    for (const dir of [1, -1]) {
      for (let angle = 15; angle <= 85; angle += 10) {
        for (let power = 20; power <= 100; power += 10) {
          const imp = simulateProjectile(ter, objects, config, mx, my, angle, power, dir, wind, {
            ignoreObjects: true,
            dtScale: 6,
          });
          if (imp.hit !== 'terrain') continue;
          const landPose = getStandPose(ter, imp.x, null, config.BODY);
          if (!landPose || Math.abs(imp.y - landPose.feetY) > 12) continue;
          // Check if landPose is safe from chickens and can actually reach/clear targets
          const lm = getMuzzle(landPose.x, landPose.feetY, landPose.groundAngle, config.BODY.muzzleHeight);
          let reachCount = 0;
          let clearCount = 0;
          let minTargetDist = Infinity;
          for (const t of activeTargets) {
            const td = Math.hypot(t.box.cx - landPose.x, t.box.cy - landPose.feetY);
            if (td < minTargetDist) minTargetDist = td;
            if (hasLineOfSight(ter, lm.mx, lm.my, t.box.cx, t.box.cy)) clearCount++;
            let canHit = false;
            for (const fdir of [1, -1]) {
              if (canHit) break;
              for (let fa = 20; fa <= 70; fa += 25) {
                for (const fp of [55, 85]) {
                  const fimp = simulateProjectile(ter, objects, config, lm.mx, lm.my, fa + fdir * landPose.groundAngle, fp, fdir, wind, { dtScale: 10 });
                  if (fimp.objectId === t.obj.id || distPointToBox(fimp.x, fimp.y, t.box) < 60) {
                    canHit = true;
                    break;
                  }
                }
                if (canHit) break;
              }
            }
            if (canHit) reachCount++;
          }
          // Avoid landing right on top of a target (<70px) or next to a chicken (<60px)
          let safetyPen = minTargetDist < 70 ? -300 : 0;
          for (const ch of ctx.chickens) {
            if (Math.hypot(ch.box.cx - landPose.x, ch.box.cy - landPose.feetY) < 60) safetyPen -= 250;
          }
          const distBonus = minTargetDist < Infinity ? Math.max(0, 200 - minTargetDist * 0.25) : 0;
          const flyScore = reachCount * 500 + clearCount * 150 + distBonus + safetyPen - Math.abs(landPose.groundAngle) * 2;
          if (!bestFly || flyScore > bestFly.sc) {
            bestFly = { sc: flyScore, angle, power, dir, landPose };
          }
        }
      }
    }
    if (bestFly && bestFly.sc >= 430 && (!best || best.sc < 380)) {
      return {
        command: { fly: { angle: bestFly.angle, power: bestFly.power, dir: bestFly.dir } },
        meta: { type: 'fly', pred: bestFly.landPose, ms: Date.now() - tStart },
      };
    }
  }

  // Unplayable safety check
  if (
    ctx.bug &&
    initBugPos &&
    obs.strokes < hole.cap - 1 &&
    history.length >= 2 &&
    !history[history.length - 1].madeProgress &&
    !history[history.length - 2].madeProgress &&
    ctx.bug.obj.y > initBugPos.y + 45 &&
    (!best || best.sc < 250)
  ) {
    return {
      command: { unplayable: true },
      meta: { type: 'unplayable', ms: Date.now() - tStart },
    };
  }

  if (!best) {
    return {
      command: { attack: { kind: 'single', angle: 45, power: 50, dir: 1 } },
      meta: { type: 'fallback', ms: Date.now() - tStart },
    };
  }

  const cmd = {
    attack: {
      kind: best.kind,
      angle: Math.max(0, Math.min(85, Math.round(best.angle))),
      power: Math.max(1, Math.min(100, Math.round(best.power))),
      dir: best.dir >= 0 ? 1 : -1,
    },
  };
  if (hole.teeActive && !best.pose.isCurrent) {
    cmd.moveX = best.pose.x;
  }

  return {
    command: cmd,
    meta: {
      type: 'attack',
      score: Math.round(best.sc),
      ms: Date.now() - tStart,
      predImpacts: best.impacts.map(i => ({ x: Math.round(i.x), y: Math.round(i.y), hit: i.hit, objectId: i.objectId })),
    },
  };
}
