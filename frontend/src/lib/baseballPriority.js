// Baseball priority filter — mirrors `lib/competitions.isBigFive` for MLB.
//
// The Live page shows every baseball game returned by the backend (MLB,
// Triple-A IL, Mexican League, Venezuelan LMBP, KBO, NPB, etc.). For
// most users only MLB is interesting, so by default the page filters to
// MLB and exposes a "Ver todas" toggle that lifts the filter.
//
// PRIMARY signal: API-Sports `league_id` (1 = MLB).
// FALLBACK signal: tolerant name matching (case + accent insensitive).
//
// Adding more priority leagues (e.g. NPB, KBO during their season) is a
// one-line change in PRIORITY_BASEBALL_LEAGUE_IDS / PRIORITY_BASEBALL_NAMES.

// API-Sports league IDs that count as "priority" for the default live view.
// Currently: MLB only (per user spec — option 3b).
export const PRIORITY_BASEBALL_LEAGUE_IDS = new Set([1]);

// Lowercased aliases used when league_id is missing.
const PRIORITY_BASEBALL_NAMES = new Set([
  'mlb',
  'major league baseball',
  'mlb regular season',
  'mlb world series',
  'mlb postseason',
  'mlb playoffs',
]);

function _normaliseName(value) {
  if (!value || typeof value !== 'string') return '';
  return value
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .trim();
}

/**
 * Returns `true` when the match belongs to a priority baseball league.
 *
 * Accepted match shapes:
 *   { league_id: 1, league: 'MLB' }
 *   { league_id: 1, league: { id: 1, name: 'MLB' } }
 *   { league: 'MLB' }   // pure-name fallback
 */
export function isPriorityBaseball(match) {
  if (!match || typeof match !== 'object') return false;

  // 1) Direct league_id on the match.
  const lid = match.league_id;
  if (typeof lid === 'number' && PRIORITY_BASEBALL_LEAGUE_IDS.has(lid)) {
    return true;
  }

  // 2) league as an object: { id, name }.
  const league = match.league;
  if (league && typeof league === 'object') {
    if (typeof league.id === 'number' && PRIORITY_BASEBALL_LEAGUE_IDS.has(league.id)) {
      return true;
    }
    if (typeof league.name === 'string' && PRIORITY_BASEBALL_NAMES.has(_normaliseName(league.name))) {
      return true;
    }
  }

  // 3) league as a bare string.
  if (typeof league === 'string' && PRIORITY_BASEBALL_NAMES.has(_normaliseName(league))) {
    return true;
  }

  return false;
}
