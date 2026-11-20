import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { CONFIG as C } from './config.mjs';

const root = new URL('../../', import.meta.url);
const read = path => JSON.parse(readFileSync(new URL(path, root), 'utf8'));
export const MAP_KEYS = ['openai', 'claude', 'gemini', 'kimi', 'doubao', 'deepseek'];
export const sha = bytes => createHash('sha256').update(bytes).digest('hex');

export function loadMap(key) {
  if (!MAP_KEYS.includes(key)) throw new Error('未知六图key');
  const selectedPath = 'output/all-map-acceptance-20261108-final/selected-maps.json';
  const selected = read(selectedPath).find(m => m.key === key);
  if (!selected) throw new Error('缺少冻结地图登记');
  let path;
  if (['openai', 'claude', 'kimi'].includes(key)) {
    const audit = read('output/model-home-structure-v1-20261103/audit-final.json');
    path = 'output/model-home-structure-v1-20261103/' + audit.selectedGrids[key];
  } else if (key === 'gemini') path = read('output/terra-gemini-candidate-20261108/candidate-v2.json').candidate.gridPath;
  else if (key === 'deepseek') path = read('output/terra-deepseek-spawn240-1352-20261108/candidate.json').gridPath;
  else path = 'output/doubao-home-structure-v3-20261103/thin-door-candidate.json';
  if (typeof path !== 'string' || !path.startsWith('output/') || path.includes('..') || path.includes('\\')) throw new Error('地图来源越界');
  const bytes = readFileSync(new URL(path, root)), doc = JSON.parse(bytes), grid = new Uint8Array(C.GW * C.GH); let cursor = 0;
  if (!Array.isArray(doc.rle)) throw new Error('地图缺少RLE');
  for (const pair of doc.rle) {
    if (!Array.isArray(pair) || pair.length !== 2 || ![0, 1].includes(pair[0]) || !Number.isSafeInteger(pair[1]) || pair[1] <= 0 || cursor + pair[1] > grid.length) throw new Error('地图RLE无效');
    grid.fill(pair[0], cursor, cursor + pair[1]); cursor += pair[1];
  }
  if (cursor !== grid.length || sha(grid) !== selected.gridRawHash || doc.gridSha256 !== selected.gridRawHash) throw new Error('地图网格来源hash不符');
  return { key, grid, spawns: selected.actualSpawns.map(p => [p.x, p.y]), sources: { selectedPath, selectedHash: sha(readFileSync(new URL(selectedPath, root))), path, fileHash: sha(bytes), gridHash: sha(grid) } };
}

export function flatMap({ floor = 760, spawns = [[300, floor - 12], [1300, floor - 12]], key = 'flat' } = {}) {
  const grid = new Uint8Array(C.GW * C.GH);
  for (let y = floor / C.CELL; y < C.GH; y++) grid.fill(1, y * C.GW, (y + 1) * C.GW);
  return { key, grid, spawns };
}
