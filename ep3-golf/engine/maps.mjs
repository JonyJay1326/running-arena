import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { loadMap as loadFrozenMap, MAP_KEYS } from '../simultaneous-v1/maps.mjs';
import { CONFIG as C } from './config.mjs';
export { MAP_KEYS };
export const sha = bytes => createHash('sha256').update(bytes).digest('hex');
export function encodeRLE(grid) {
  const rle = [];
  for (const bit of grid) {
    if (rle.length && rle.at(-1)[0] === bit) rle.at(-1)[1]++;
    else rle.push([bit, 1]);
  }
  return rle;
}
export function decodeRLE(rle) {
  const grid = new Uint8Array(C.GW * C.GH); let cursor = 0;
  if (!Array.isArray(rle)) throw new Error('地图缺少RLE');
  for (const pair of rle) {
    if (!Array.isArray(pair) || pair.length !== 2 || ![0, 1].includes(pair[0]) || !Number.isSafeInteger(pair[1]) || pair[1] <= 0 || cursor + pair[1] > grid.length) throw new Error('地图RLE无效');
    grid.fill(pair[0], cursor, cursor + pair[1]); cursor += pair[1];
  }
  if (cursor !== grid.length) throw new Error('地图RLE长度无效');
  return grid;
}
export function flatMap(floor = 780) {
  if (!Number.isInteger(floor / C.CELL) || floor < 0 || floor >= C.H) throw new Error('平地高度无效');
  const grid = new Uint8Array(C.GW * C.GH); grid.fill(1, floor / C.CELL * C.GW);
  return { key: 'flat', grid, sources: { gridHash: sha(grid), floor } };
}
export function loadMap(key) {
  if (key === 'flat') return flatMap();
  if (MAP_KEYS.includes(key)) return loadFrozenMap(key);
  if (key !== 'dragon' && key !== 'dragon-golf') throw new Error('未知地图');
  // dragon-golf：高尔夫用龙头图（去掉不到 4 格的漂浮碎块，龙头上挖出两个眼窝），由 tools/golf-hole-design/make-dragon-golf.py 生成
  const bytes = readFileSync(new URL(key === 'dragon' ? './maps/dragon.json' : './maps/dragon-golf.json', import.meta.url));
  const doc = JSON.parse(bytes), grid = decodeRLE(doc.rle);
  if (doc.gridSha256 !== sha(grid)) throw new Error('龙头地图hash不符');
  return { key, grid, sources: { gridHash: sha(grid), fileHash: sha(bytes), source: doc.source } };
}
export function validateHole(hole) {
  if (hole && typeof hole === 'object' && !Array.isArray(hole)) hole = { fly: 0, ...hole };
  if (!hole || typeof hole.id !== 'string' || !hole.id || !['flat', 'dragon', 'dragon-golf', ...MAP_KEYS].includes(hole.map) ||
      !Number.isInteger(hole.par) || hole.par < 1 || hole.par > 6 || hole.cap !== hole.par + 4 ||
      !Number.isFinite(hole.wind) || Math.abs(hole.wind) > 8 || !Number.isInteger(hole.wind * 2) ||
      !['tokens', 'bug', 'both'].includes(hole.goal) || !Number.isInteger(hole.fly) || hole.fly < 0 || hole.fly > 1 ||
      !hole.tee || ![hole.tee.x0, hole.tee.x1].every(Number.isFinite) || hole.tee.x0 < 22 || hole.tee.x1 > C.W - 22 || hole.tee.x0 > hole.tee.x1 || (hole.tee.minFeet !== undefined && !(hole.tee.minFeet >= 0 && hole.tee.minFeet <= C.H)) || !Array.isArray(hole.objects)) throw new Error('球洞字段无效');
  // 每洞局部改地形：[{op:'carve'|'fill', rect:[x0,y0,x1,y1]} | {op, circle:[x,y,r]}]，按顺序施加，幂等
  if (hole.terrain !== undefined && (!Array.isArray(hole.terrain) || hole.terrain.length > 64 || hole.terrain.some(e => !e || !['carve', 'fill'].includes(e.op) ||
      !((Array.isArray(e.rect) && e.rect.length === 4 && e.rect.every(Number.isFinite) && e.rect[0] < e.rect[2] && e.rect[1] < e.rect[3]) ||
        (Array.isArray(e.circle) && e.circle.length === 3 && e.circle.every(Number.isFinite) && e.circle[2] > 0)) ||
      Object.keys(e).some(k => !['op', 'rect', 'circle', 'note'].includes(k))))) throw new Error('地形改动无效');
  const EXTRA = { bigToken: ['hp'], pushToken: ['hp'], gate: ['width', 'height'], switch: ['opens'] };
  for (const o of hole.objects) {
    if (!Object.hasOwn(C.OBJECTS, o.type) || ![o.x, o.y].every(Number.isFinite) || o.x < 0 || o.x >= C.W || o.y < 0 || o.y > C.H) throw new Error('球洞物体无效');
    if (Object.keys(o).some(k => !['type', 'x', 'y', ...(EXTRA[o.type] ?? [])].includes(k))) throw new Error('球洞物体字段无效');
    if (o.hp !== undefined && (!Number.isInteger(o.hp) || o.hp < 1 || o.hp > 2000)) throw new Error('大token血量无效');
    if (o.type === 'gate' && [o.width, o.height].some(v => v !== undefined && (!Number.isFinite(v) || v < 8 || v > 400))) throw new Error('门尺寸无效');
    if (o.type === 'switch' && hole.objects[o.opens]?.type !== 'gate') throw new Error('开关必须指向一扇门');
  }
  const bugs = hole.objects.filter(o => o.type === 'bug').length;
  if (bugs > 1 || (hole.goal !== 'tokens' && bugs !== 1)) throw new Error('Bug目标需要且只允许一个Bug');
  if (hole.goal !== 'bug' && !hole.objects.some(o => ['token', 'bigToken', 'pushToken'].includes(o.type))) throw new Error('token目标不能为空');
  if (hole.recycle !== null && (!hole.recycle || !['x0','y0','x1','y1'].every(k => Number.isFinite(hole.recycle[k])) || hole.recycle.x0 >= hole.recycle.x1 || hole.recycle.y0 >= hole.recycle.y1 || hole.recycle.x0 < 0 || hole.recycle.y0 < 0 || hole.recycle.x1 > C.W || hole.recycle.y1 > C.H)) throw new Error('回收站矩形无效');
  if (hole.goal !== 'tokens' && hole.recycle === null) throw new Error('Bug目标缺少回收站');
  return structuredClone(hole);
}
export function loadHole(path) { return validateHole(JSON.parse(readFileSync(path, 'utf8'))); }

export function applyTerrainEdits(grid, edits = []) {
  for (const e of edits) {
    const v = e.op === 'fill' ? 1 : 0;
    for (let gy = 0; gy < C.GH; gy++) for (let gx = 0; gx < C.GW; gx++) {
      const x = gx * C.CELL + C.CELL / 2, y = gy * C.CELL + C.CELL / 2;
      const inside = e.rect ? x >= e.rect[0] && x < e.rect[2] && y >= e.rect[1] && y < e.rect[3] : (x - e.circle[0]) ** 2 + (y - e.circle[1]) ** 2 <= e.circle[2] ** 2;
      if (inside) grid[gy * C.GW + gx] = v;
    }
  }
  return grid;
}
