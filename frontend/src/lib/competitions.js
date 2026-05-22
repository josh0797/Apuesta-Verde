// Frontend port of /app/backend/services/football_competitions.py
//
// Pure-JS helpers to identify whether a free-form league name resolves to one
// of football's Top-5 European competitions. Used by LivePage to keep the
// "Live now" list consistent with the Big Five-only live analysis.
//
// PRIMARY signal: API-Sports `league_id`. This is deterministic and is the
// only way to reliably distinguish "Bundesliga" (Germany, id=78) from the
// Austrian Bundesliga (id=218) or German lower tiers — both of which the
// upstream feed labels simply as "Bundesliga".
//
// FALLBACK signal: tolerant name matching (accent-insensitive, region tail
// strip). Used only when no league_id is available on the match object.

// API-Sports league IDs for the Big Five — AUTHORITATIVE source of truth.
//   • Premier League (England) → 39
//   • LaLiga (Spain)           → 140
//   • Serie A (Italy)          → 135
//   • Bundesliga (Germany)     → 78
//   • Ligue 1 (France)         → 61
export const BIG_FIVE_LEAGUE_IDS = new Set([39, 140, 135, 78, 61]);

const BIG_FIVE_ALIASES = {
  'Premier League': [
    'premier league',
    'english premier league',
    'epl',
    'england premier league',
  ],
  'LaLiga': [
    'laliga',
    'la liga',
    'spain laliga',
    'spain primera division',
    'primera division',
    'primera división',
    'la liga santander',
    'laliga ea sports',
  ],
  'Serie A': [
    'serie a',
    'italy serie a',
    'italian serie a',
    'lega serie a',
    'serie a tim',
  ],
  'Bundesliga': [
    'bundesliga',
    'germany bundesliga',
    '1. bundesliga',
    '1 bundesliga',
    'german bundesliga',
  ],
  'Ligue 1': [
    'ligue 1',
    'france ligue 1',
    'ligue 1 uber eats',
    'french ligue 1',
  ],
};

// Names that LOOK like Big Five but are NOT (Austria/Switzerland Bundesliga,
// lower German tiers, etc). Stripping a region suffix would otherwise pull
// them into the allowlist by accident.
const FALSE_FRIENDS = new Set([
  'austria bundesliga',
  'austrian bundesliga',
  'bundesliga austria',
  'admiral bundesliga',
  'swiss super league',
  'switzerland bundesliga',
  '2. bundesliga',
  '2 bundesliga',
  '3. liga',
  '3 liga',
  'serie b',
  'serie c',
  'segunda division',
  'segunda división',
  'la liga 2',
  'laliga 2',
  'laliga hypermotion',
  'championship',
  'efl championship',
  'ligue 2',
]);

function _stripAccents(s) {
  return (s || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '');
}

function _normalize(name) {
  if (!name) return '';
  let s = String(name).trim();
  // Drop a trailing region/country tail: " - England", " (Spain)", ", Apertura"
  s = s.replace(/\s*(?:[-,–]+\s*[a-záéíóúñ ]+|\([a-záéíóúñ ]+\))\s*$/i, '');
  s = _stripAccents(s).toLowerCase();
  s = s.replace(/[^a-z0-9]+/g, ' ').trim();
  return s;
}

/**
 * True iff the league resolves to one of Premier/LaLiga/Serie A/Bundesliga/Ligue 1.
 *
 * Accepts EITHER a match object (preferred, uses league_id when available)
 * OR a raw league name string (fallback to fuzzy name matching). The match
 * object form is the only way to distinguish German Bundesliga (78) from the
 * Austrian Bundesliga (218) or 2./3. tier German leagues — all of which the
 * upstream feed labels simply as "Bundesliga".
 *
 *   isBigFive(matchObj)                       // preferred — uses league_id
 *   isBigFive('Premier League - England')     // fallback by name
 */
export function isBigFive(matchOrName) {
  // Object form: prefer league_id (deterministic, unambiguous)
  if (matchOrName && typeof matchOrName === 'object') {
    const id = matchOrName.league_id;
    if (id != null) {
      const n = Number(id);
      if (Number.isFinite(n)) return BIG_FIVE_LEAGUE_IDS.has(n);
    }
    // Object without league_id → fall back to its `league` string
    return isBigFiveByName(matchOrName.league);
  }
  return isBigFiveByName(matchOrName);
}

function isBigFiveByName(leagueName) {
  const norm = _normalize(leagueName);
  if (!norm) return false;
  if (FALSE_FRIENDS.has(norm)) return false;
  for (const aliases of Object.values(BIG_FIVE_ALIASES)) {
    for (const alias of aliases) {
      if (norm === alias) return true;
      // Substring fallback ONLY when the alias appears as a full token sequence
      // and the input doesn't trigger a false-friend.
      if (norm.includes(alias) && !FALSE_FRIENDS.has(norm)) {
        // Ensure we're not catching "premier league 2" or "2 bundesliga"-style noise.
        const reg = new RegExp(`(^|\\s)${alias.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\$&')}(\\s|$)`);
        if (reg.test(norm)) return true;
      }
    }
  }
  return false;
}

/** Returns the canonical Big Five name, or null if the league is not Big Five. */
export function bigFiveCanonical(leagueName) {
  const norm = _normalize(leagueName);
  if (!norm || FALSE_FRIENDS.has(norm)) return null;
  for (const [canonical, aliases] of Object.entries(BIG_FIVE_ALIASES)) {
    for (const alias of aliases) {
      if (norm === alias) return canonical;
      const reg = new RegExp(`(^|\\s)${alias.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\$&')}(\\s|$)`);
      if (reg.test(norm)) return canonical;
    }
  }
  return null;
}
