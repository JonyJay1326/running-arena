export const SCORING = Object.freeze({
  version: 'completed-plus3-unfinished-plus4-v1',
  completedMaxOverPar: 3,
  unfinishedOverPar: 4,
  ranking: ['totalScoreAscending', 'completedHolesDescending'],
});

export function holeScore({par, strokes, completed, error = null}) {
  if (!Number.isInteger(par) || par < 1 || par > 6) throw new Error('计分标准杆必须为1至6');
  if (!completed || error) return par + SCORING.unfinishedOverPar;
  if (!Number.isInteger(strokes) || strokes < 1) throw new Error('完成洞的实际杆数无效');
  return Math.min(strokes, par + SCORING.completedMaxOverPar);
}

export function compareStandings(a, b) {
  return (a.total ?? a.strokes) - (b.total ?? b.strokes) || b.completed - a.completed;
}

export function rankStandings(rows) {
  const sorted = rows.map(row => ({...row})).sort(compareStandings);
  for (let i = 0; i < sorted.length; i++) {
    sorted[i].rank = i && compareStandings(sorted[i - 1], sorted[i]) === 0 ? sorted[i - 1].rank : i + 1;
  }
  return sorted;
}
