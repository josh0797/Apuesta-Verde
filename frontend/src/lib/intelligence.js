/**
 * Decision Intelligence derivation layer.
 *
 * The LLM analyst returns rich-but-conservative JSON. To power the
 * intelligence UI (drivers, volatility, fragility, match_state, best_for/avoid)
 * we derive most fields client-side from existing data, and gracefully use any
 * fields the LLM HAS provided (e.g. when the prompt has been extended to emit
 * `match_state` and `best_for/avoid` explicitly).
 *
 * Why client-side derivation?
 *   - Adds zero tokens to the LLM call (keeps cost predictable).
 *   - Degrades cleanly when the LLM returns minimal output.
 *   - Easy to iterate without re-running analyses.
 *
 * All thresholds are tuned to the conservative analyst persona (60+ confidence,
 * "moderate" mode). When tuning, prefer pushing volatility/fragility UP rather
 * than down — disciplined betting messaging.
 */

// ─── Driver taxonomy (canonical keys ↔ i18n labels live in components) ──────
export const DRIVER_KEYS = [
  'form',
  'motivation',
  'home-advantage',
  'absences',
  'fatigue',
  'rotation-risk',
];

export const DRIVER_META = {
  form:             { icon: 'TrendingUp',     tone: 'emerald', label_es: 'Forma reciente',     label_en: 'Recent form' },
  motivation:       { icon: 'Target',         tone: 'cyan',    label_es: 'Motivación',         label_en: 'Motivation' },
  'home-advantage': { icon: 'Home',           tone: 'slate',   label_es: 'Localía',            label_en: 'Home advantage' },
  absences:         { icon: 'UserMinus',      tone: 'amber',   label_es: 'Bajas clave',        label_en: 'Key absences' },
  fatigue:          { icon: 'BatteryWarning', tone: 'amber',   label_es: 'Fatiga',             label_en: 'Fatigue' },
  'rotation-risk':  { icon: 'Shuffle',        tone: 'rose',    label_es: 'Riesgo de rotación', label_en: 'Rotation risk' },
};

// ─── Match state taxonomy ───────────────────────────────────────────────────
export const MATCH_STATES = {
  CONTROLLED_MATCH: { icon: 'ShieldCheck', tone: 'emerald', label_es: 'Controlado',      label_en: 'Controlled' },
  CHAOTIC_MATCH:    { icon: 'Activity',    tone: 'amber',   label_es: 'Caótico',         label_en: 'Chaotic' },
  HIGH_MOTIVATION:  { icon: 'Flame',       tone: 'cyan',    label_es: 'Alta motivación', label_en: 'High motivation' },
  LOW_URGENCY:      { icon: 'Clock',       tone: 'slate',   label_es: 'Baja urgencia',   label_en: 'Low urgency' },
};

// ─── Engine style presets ──────────────────────────────────────────────────
export const ENGINE_STYLES = {
  conservative:        { icon: 'Shield',  tone: 'slate',   label_es: 'Conservador',        label_en: 'Conservative' },
  'protected-markets': { icon: 'Lock',    tone: 'emerald', label_es: 'Mercados protegidos', label_en: 'Protected markets' },
  'low-fragility':     { icon: 'Layers',  tone: 'cyan',    label_es: 'Baja fragilidad',    label_en: 'Low fragility' },
  'value-hunting':     { icon: 'Search',  tone: 'amber',   label_es: 'Caza de valor',      label_en: 'Value hunting' },
  'live-momentum':     { icon: 'Zap',     tone: 'cyan',    label_es: 'Momentum en vivo',   label_en: 'Live momentum' },
};

// Markets the conservative engine treats as PROTECTED (low-fragility, asymmetric edge)
const PROTECTED_MARKETS = [
  '1X2', 'Doble Oportunidad', 'Under 2.5', 'Under 3.5',
  'Draw No Bet', 'Handicap Asiatico', 'DO 1er Tiempo',
  'Moneyline', // NBA/MLB equivalent
];

// Markets to AVOID by default in conservative mode
const FRAGILE_MARKETS = [
  'BTTS', 'Over 2.5', 'Over 3.5', 'Goleador', 'Resultado exacto',
  'Corners', 'Tarjetas', 'Handicap -1.5', 'Spread aggressive', 'Total Over',
];

/**
 * Build the full Intelligence View for a pick.
 *
 * @param {object} pick   — A pick object as returned by the LLM analyst.
 * @param {string} sport  — 'football' | 'basketball' | 'baseball'.
 * @returns {object} { confidence, risk, volatility, fragility, drivers, matchState, bestFor, avoid, reasoning }
 */
export function deriveIntelligence(pick, sport = 'football') {
  if (!pick) return null;
  const rec = pick.recommendation || {};
  const motHome = (pick.motivation?.home?.level) ?? 3;
  const motAway = (pick.motivation?.away?.level) ?? 3;
  const key = pick.key_data || {};
  const live = pick.live_stats || null;
  const freshness = pick.data_freshness || {};
  const conf = Math.max(0, Math.min(100, Number(rec.confidence_score) || 0));

  // ── Volatility (0–100): how unstable are signals? ────────────────────────
  // Higher = more contradictory data, sparse odds, motivation gap.
  let volatility = 0;
  if ((freshness.odds || '').toLowerCase() === 'stale') volatility += 18;
  if ((freshness.context || '').toLowerCase() === 'stale') volatility += 12;
  if (Math.abs(motHome - motAway) >= 3) volatility += 14;
  if (motHome <= 2 || motAway <= 2) volatility += 10;
  if ((pick.risks?.length || 0) >= 2) volatility += 10;
  if (live && Math.abs((live.xg_home ?? 0) - (live.xg_away ?? 0)) > 1.5) volatility += 8;
  if (!(key.odds_1x2?.home) && !(key.odds_1x2?.away)) volatility += 12;
  volatility = Math.min(100, volatility);

  // ── Fragility (0–100): how easily does this bet collapse? ────────────────
  // Higher = single-snapshot odds, key absences, fragile market chosen.
  let fragility = 0;
  if ((key.line_movement || 'desconocido').toLowerCase() === 'desconocido') fragility += 14;
  if ((key.injuries_home ?? 0) >= 3) fragility += 10;
  if ((key.injuries_away ?? 0) >= 3) fragility += 10;
  if (FRAGILE_MARKETS.some((m) => (rec.market || '').toLowerCase().includes(m.toLowerCase()))) fragility += 18;
  if (conf < 65) fragility += 12;
  if ((rec.odds_range || '').includes('2.') && conf < 70) fragility += 8;
  fragility = Math.min(100, fragility);

  // ── Risk score (composite): blend confidence inverse + volatility + fragility
  const risk = Math.min(100, Math.round(((100 - conf) * 0.45) + (volatility * 0.3) + (fragility * 0.25)));

  // ── Drivers (positive + negative contributions) ──────────────────────────
  const drivers = [];
  // Form: derive strength from W/D/L string ratio
  const formStr = (key.form_home || '') + (key.form_away || '');
  if (formStr) {
    const wins = (formStr.match(/W/g) || []).length;
    const losses = (formStr.match(/L/g) || []).length;
    drivers.push({
      key: 'form',
      strength: Math.min(100, 40 + (wins - losses) * 8),
      sign: wins > losses ? 'positive' : (losses > wins ? 'negative' : 'neutral'),
      detail_es: `Forma combinada: ${wins}W / ${losses}L recientes`,
      detail_en: `Combined form: ${wins}W / ${losses}L recent`,
    });
  }
  // Motivation
  const motAvg = (motHome + motAway) / 2;
  drivers.push({
    key: 'motivation',
    strength: Math.round(motAvg * 20),
    sign: motAvg >= 4 ? 'positive' : (motAvg <= 2.5 ? 'negative' : 'neutral'),
    detail_es: `Promedio motivacional: ${motAvg.toFixed(1)} / 5`,
    detail_en: `Avg motivation: ${motAvg.toFixed(1)} / 5`,
  });
  // Home advantage (always neutral-positive unless penalty)
  drivers.push({
    key: 'home-advantage',
    strength: 55,
    sign: motHome >= motAway ? 'positive' : 'neutral',
    detail_es: 'Localía aporta ~5-8% en mercados protegidos.',
    detail_en: 'Home advantage adds ~5-8% in protected markets.',
  });
  // Absences
  const absH = key.injuries_home ?? 0;
  const absA = key.injuries_away ?? 0;
  if (absH || absA) {
    drivers.push({
      key: 'absences',
      strength: Math.min(100, 30 + (absH + absA) * 6),
      sign: (absH + absA) >= 4 ? 'negative' : 'neutral',
      detail_es: `Bajas: local ${absH} / visita ${absA}`,
      detail_en: `Absences: home ${absH} / away ${absA}`,
    });
  }
  // Fatigue (proxy: live + late game, or chained matches not available — use conf penalty)
  if (live || (pick.is_live && (pick.live_minute || 0) > 60)) {
    drivers.push({
      key: 'fatigue',
      strength: 55,
      sign: 'negative',
      detail_es: 'Partido en vivo en fase final: fatiga puede sesgar resultados.',
      detail_en: 'Live match in late phase: fatigue may bias outcomes.',
    });
  }
  // Rotation risk (proxy: low motivation = likely rotation)
  if (motHome <= 2 || motAway <= 2) {
    drivers.push({
      key: 'rotation-risk',
      strength: 65,
      sign: 'negative',
      detail_es: 'Equipo con baja urgencia: alta probabilidad de rotaciones.',
      detail_en: 'Low-urgency side: high chance of rotations.',
    });
  }

  // ── Match state (use LLM-provided if present, else derive) ───────────────
  let matchState = pick.match_state;
  if (!matchState) {
    if (motHome >= 4 && motAway >= 4) matchState = 'HIGH_MOTIVATION';
    else if (volatility >= 50 || (live && Math.abs((live.shots_home ?? 0) - (live.shots_away ?? 0)) >= 10)) matchState = 'CHAOTIC_MATCH';
    else if (motHome <= 2 && motAway <= 2) matchState = 'LOW_URGENCY';
    else matchState = 'CONTROLLED_MATCH';
  }

  // ── Best for / Avoid (use LLM if present, else derive from market policy) ─
  let bestFor = Array.isArray(pick.best_for) ? pick.best_for : null;
  let avoid = Array.isArray(pick.avoid) ? pick.avoid : null;
  if (!bestFor) {
    bestFor = PROTECTED_MARKETS.filter((m) => {
      if ((rec.market || '').toLowerCase().includes(m.toLowerCase())) return true;
      if (m === 'Under 2.5' && conf >= 70 && (matchState === 'CONTROLLED_MATCH' || matchState === 'LOW_URGENCY')) return true;
      if (m === 'Doble Oportunidad' && conf >= 60) return true;
      if (m === 'Draw No Bet' && conf >= 65) return true;
      if (m === 'Moneyline' && sport !== 'football' && conf >= 65) return true;
      return false;
    }).slice(0, 4);
  }
  if (!avoid) {
    avoid = FRAGILE_MARKETS.filter((m) => {
      if (matchState === 'CHAOTIC_MATCH' && (m.startsWith('Over') || m === 'BTTS')) return true;
      if (matchState === 'LOW_URGENCY' && m.startsWith('Over')) return true;
      if (m.startsWith('Handicap -1.5') || m === 'Resultado exacto' || m === 'Corners' || m === 'Tarjetas') return true;
      return false;
    }).slice(0, 4);
  }

  return {
    confidence: conf,
    risk,
    volatility,
    fragility,
    drivers: drivers.slice(0, 6),
    matchState,
    bestFor,
    avoid,
    reasoning: pick.reasoning || null,
    risks: pick.risks || [],
  };
}

/** Map raw integer to a tier label */
export function tierFromScore(score) {
  if (score == null) return 'unknown';
  if (score >= 67) return 'high';
  if (score >= 34) return 'medium';
  return 'low';
}

/** Filter a list of picks by engine style presets (declarative, side-effect free). */
export function applyEnginePreset(picks, presetKey) {
  if (!presetKey || !Array.isArray(picks)) return picks;
  const list = picks.map((p) => ({ pick: p, intel: deriveIntelligence(p) }));
  let filtered;
  switch (presetKey) {
    case 'conservative':
      filtered = list.filter(({ intel }) => intel && intel.confidence >= 70 && intel.fragility <= 40);
      break;
    case 'protected-markets':
      filtered = list.filter(({ pick }) => PROTECTED_MARKETS.some((m) => (pick.recommendation?.market || '').toLowerCase().includes(m.toLowerCase())));
      break;
    case 'low-fragility':
      filtered = list.filter(({ intel }) => intel && intel.fragility <= 30);
      break;
    case 'value-hunting':
      filtered = list.filter(({ intel }) => intel && intel.confidence >= 60 && intel.confidence < 75);
      break;
    case 'live-momentum':
      filtered = list.filter(({ pick }) => pick.is_live);
      break;
    default:
      filtered = list;
  }
  return filtered.map(({ pick }) => pick);
}

/** Built-in saved-view presets shown alongside user-saved views */
export const BUILTIN_VIEWS = [
  { id: 'builtin:high-confidence', name_es: 'Alta confianza', name_en: 'High confidence', builtin: true, filters: { minConfidence: 75 } },
  { id: 'builtin:protected', name_es: 'Solo mercados protegidos', name_en: 'Protected markets only', builtin: true, enginePreset: 'protected-markets' },
  { id: 'builtin:live', name_es: 'Solo en vivo', name_en: 'Live only', builtin: true, enginePreset: 'live-momentum' },
];
