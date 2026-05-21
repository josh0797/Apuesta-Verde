export function formatOdd(v) {
  if (v === null || v === undefined || isNaN(v)) return '—';
  return Number(v).toFixed(2);
}

export function formatDateTime(iso, lang = 'es') {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    const locale = lang === 'es' ? 'es-ES' : 'en-US';
    return d.toLocaleString(locale, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
  } catch { return iso; }
}

export function relativeTime(iso, lang = 'es') {
  if (!iso) return '—';
  const d = new Date(iso);
  const diffMs = d.getTime() - Date.now();
  const absMin = Math.round(Math.abs(diffMs) / 60000);
  const isFuture = diffMs > 0;
  const unit = absMin < 60 ? 'min' : absMin < 1440 ? 'h' : 'd';
  const v = unit === 'min' ? absMin : unit === 'h' ? Math.round(absMin / 60) : Math.round(absMin / 1440);
  if (lang === 'en') return isFuture ? `in ${v}${unit}` : `${v}${unit} ago`;
  return isFuture ? `en ${v}${unit}` : `hace ${v}${unit}`;
}

export function confidenceTier(score) {
  if (score >= 80) return 'Maxima';
  if (score >= 70) return 'Alta';
  if (score >= 60) return 'Media';
  return 'Below';
}

export function tierClass(tier) {
  if (tier === 'Maxima') return 'bg-amber-500/15 text-amber-200 border border-amber-500/30';
  if (tier === 'Alta') return 'bg-emerald-500/15 text-emerald-200 border border-emerald-500/30';
  if (tier === 'Media') return 'bg-cyan-500/15 text-cyan-200 border border-cyan-500/30';
  return 'bg-slate-500/15 text-slate-200 border border-slate-500/30';
}


// ───────────────────────────────────────────────────────────────────────────
// humanizeSelection — rewrite an LLM-emitted selection string using the real
// team names instead of opaque "Home / Draw / Away" or "1X / X2".
//
// Examples:
//   ("Home/Draw",       "Doble Oportunidad", "Bayern", "Bremen", "es")
//     → "Bayern o empate"
//   ("Draw or Away",    "Double Chance",     "Bayern", "Bremen", "en")
//     → "Draw or Bremen"
//   ("Home",            "Moneyline",         "Knicks", "Cavs",   "es")
//     → "Knicks"
//   ("Under 2.5",       "Total Under",       ...)
//     → "Menos de 2.5 goles"  (sport-aware via the `terms` arg)
//   ("Bayern Múnich",   "1X2",               "Bayern", "Bremen", "es")
//     → "Bayern Múnich"      (already specific, returned as-is)
//
// Intentionally pure (no React imports) so it can be called from anywhere.
//
// Parameters:
//   selection   raw string emitted by the LLM
//   market      market label (used to pick the right humanization branch)
//   homeName    home team display name (defaults to "Local")
//   awayName    away team display name (defaults to "Visitante")
//   lang        'es' | 'en' (default 'es')
//   sport       'football' | 'basketball' | 'baseball' (optional — only
//               affects whether Under/Over reads "goles" vs "puntos" vs "carreras")
//
// Returns: humanized string, or the original selection if nothing matched.

const _DRAW_WORDS = /^(draw|empate|x|tie|d)$/i;
const _HOME_WORDS = /^(home|local|1|h|casa)$/i;
const _AWAY_WORDS = /^(away|visitor|visitante|2|v|a|road)$/i;

function _drawLabel(lang) {
  return lang === 'en' ? 'Draw' : 'empate';
}

function _resolveSide(token, homeName, awayName, lang) {
  const t = String(token || '').trim();
  if (!t) return null;
  if (_DRAW_WORDS.test(t)) return _drawLabel(lang);
  if (_HOME_WORDS.test(t)) return homeName;
  if (_AWAY_WORDS.test(t)) return awayName;
  // Already a literal team name or unknown token → return as-is.
  return t;
}

function _unitForSport(sport, lang) {
  const s = String(sport || 'football').toLowerCase();
  if (s === 'basketball') return lang === 'en' ? 'points' : 'puntos';
  if (s === 'baseball')   return lang === 'en' ? 'runs'   : 'carreras';
  return lang === 'en' ? 'goals' : 'goles';
}

export function humanizeSelection(selection, market, homeName, awayName, lang = 'es', sport = 'football') {
  if (!selection) return selection;
  const sel = String(selection).trim();
  if (!sel) return sel;
  const home = homeName || (lang === 'en' ? 'Home' : 'Local');
  const away = awayName || (lang === 'en' ? 'Away' : 'Visitante');
  const m = String(market || '').toLowerCase();
  const orWord = lang === 'en' ? 'or' : 'o';

  // ── 1X2 short-codes ────────────────────────────────────────────────
  // "1X", "X2", "12" with optional spaces; case insensitive.
  const ddCode = sel.replace(/\s+/g, '').toUpperCase();
  if (ddCode === '1X')  return `${home} ${orWord} ${_drawLabel(lang)}`;
  if (ddCode === 'X2')  return `${_drawLabel(lang)} ${orWord} ${away}`;
  if (ddCode === '12')  return `${home} ${orWord} ${away}`;
  if (ddCode === '1')   return home;
  if (ddCode === 'X')   return _drawLabel(lang);
  if (ddCode === '2')   return away;

  // ── Double Chance / Doble Oportunidad: "Home/Draw", "Draw or Away", "X2"
  const isDoubleChance = m.includes('doble') || m.includes('double chance');
  // ── Moneyline / 1X2 single side
  const isMoneylineLike = m.includes('moneyline') || m.includes('draw no bet')
    || m === '1x2' || m.includes('match winner') || m.includes('1x2');
  // ── Spread / Run Line / Hándicap
  const isSpread = m.includes('spread') || m.includes('handicap')
    || m.includes('hándicap') || m.includes('run line');
  // ── Totals (Over/Under)
  const isTotalUnder = m.includes('under') || m.includes('total under') || m.includes('menos');
  const isTotalOver = m.includes('over') || m.includes('total over') || m.includes('más');

  // Spread with explicit side prefix — "Home -1.5", "Visitante +1.5"
  // Evaluated BEFORE the DoubleChance generic split to avoid the dash being
  // treated as a separator ("Home -1.5" mis-splitting into ["Home", "1.5"]).
  if (isSpread || /^(home|local|away|visitor|visitante|h|v|1|2)\s*[+-]\d/i.test(sel)) {
    const sm = sel.match(/^(home|local|away|visitor|visitante|h|v|1|2)\s*([+-]?\d+(?:\.\d+)?)/i);
    if (sm) {
      const side = _resolveSide(sm[1], home, away, lang);
      return `${side} ${sm[2]}`;
    }
  }

  // Double Chance forms: any pair separated by '/', ' or ', ' o ', ',' (NOT '-')
  if (isDoubleChance || /\b(home|local|away|visitor|visitante|draw|empate)\b/i.test(sel) || /[/]/.test(sel)) {
    const parts = sel.split(/\s*(?:\/|\s+or\s+|\s+o\s+|,)\s*/i).filter(Boolean);
    if (parts.length === 2) {
      const a = _resolveSide(parts[0], home, away, lang);
      const b = _resolveSide(parts[1], home, away, lang);
      if (a && b && a !== parts[0]?.trim()) {
        // At least one token was a placeholder we resolved → reformat.
        return `${a} ${orWord} ${b}`;
      }
      if (a && b && (a === _drawLabel(lang) || b === _drawLabel(lang))) {
        return `${a} ${orWord} ${b}`;
      }
    }
  }

  // Single-side Home/Away/Draw (Moneyline, DNB, 1X2)
  if (isMoneylineLike || /^(home|local|away|visitor|visitante|draw|empate)$/i.test(sel)) {
    const resolved = _resolveSide(sel, home, away, lang);
    if (resolved) return resolved;
  }

  // Spread with explicit side prefix — "Home -1.5", "Visitante +1.5"
  if (isSpread) {
    const sm = sel.match(/^(home|local|away|visitor|visitante|h|v|1|2)\s*([+-]?\d+(?:\.\d+)?)/i);
    if (sm) {
      const side = _resolveSide(sm[1], home, away, lang);
      return `${side} ${sm[2]}`;
    }
  }

  // Totals — "Under 2.5", "Over 3.5", "Más 8.5", "Menos 9.5"
  if (isTotalUnder || isTotalOver || /^(under|over|menos|m[aá]s)\b/i.test(sel)) {
    const m2 = sel.match(/(under|over|menos|m[aá]s)\s*(\d+(?:\.\d+)?)/i);
    if (m2) {
      const isUnder = /under|menos/i.test(m2[1]);
      const word = lang === 'en'
        ? (isUnder ? 'Under' : 'Over')
        : (isUnder ? 'Menos de' : 'Más de');
      const unit = _unitForSport(sport, lang);
      return `${word} ${m2[2]} ${unit}`;
    }
  }

  // Default — already specific or unrecognized: return as-is.
  return sel;
}

