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

// ─── Motivation state taxonomy (v2) ─────────────────────────────────────────
// Emitted by the analyst engine for each pick. Used to surface the contextual
// motivation pattern rather than reducing it to a single 1–5 number per team.
//
//   HIGH_BOTH          — both teams have plenty to play for
//   ASYMMETRIC_HIGH_LOW — one side is highly motivated, the other has little
//                         left to play for; often creates value in protected
//                         markets favoring the motivated side
//   LOW_BOTH           — neither team has anything real on the line; the only
//                         state that may justify auto-discard (and only when
//                         no other edge exists)
//   NORMAL             — anything else, treated with the standard rules
export const MOTIVATION_STATES = {
  HIGH_BOTH: {
    icon: 'Flame',
    tone: 'cyan',
    label_es: 'Ambos motivados',
    label_en: 'Both motivated',
    hint_es: 'Los dos equipos tienen algo importante por jugar.',
    hint_en: 'Both sides have meaningful stakes.',
  },
  ASYMMETRIC_HIGH_LOW: {
    icon: 'TrendingUp',
    tone: 'amber',
    label_es: 'Asimetría motivacional',
    label_en: 'Asymmetric motivation',
    hint_es: 'Un lado urge, el otro no — puede generar valor en mercados protegidos.',
    hint_en: 'One side urgent, the other not — possible edge in protected markets.',
  },
  LOW_BOTH: {
    icon: 'Clock',
    tone: 'slate',
    label_es: 'Sin urgencia bilateral',
    label_en: 'No bilateral urgency',
    hint_es: 'Ninguno tiene algo real por jugar — descartar salvo otro edge claro.',
    hint_en: 'Neither side has real stakes — discard unless a clear edge exists.',
  },
  NORMAL: {
    icon: 'Activity',
    tone: 'emerald',
    label_es: 'Motivación normal',
    label_en: 'Normal motivation',
    hint_es: 'Partido sin patrón motivacional extremo. Aplicar reglas estándar.',
    hint_en: 'No extreme motivation pattern. Apply standard rules.',
  },
};

/** Resolve motivation_state from a pick — uses LLM-provided value if present,
 * else derives from the per-team levels with the same rules as the backend.
 */
export function resolveMotivationState(pick) {
  if (!pick) return 'NORMAL';
  if (pick.motivation_state && MOTIVATION_STATES[pick.motivation_state]) {
    return pick.motivation_state;
  }
  const h = pick.motivation?.home?.level ?? 3;
  const a = pick.motivation?.away?.level ?? 3;
  const highH = h >= 4;
  const highA = a >= 4;
  const lowH = h <= 2;
  const lowA = a <= 2;
  if (highH && highA) return 'HIGH_BOTH';
  if (lowH && lowA) return 'LOW_BOTH';
  if ((highH && lowA) || (lowH && highA)) return 'ASYMMETRIC_HIGH_LOW';
  return 'NORMAL';
}

// ─── Pressure state taxonomy (competition stage layer) ──────────────────────
// Emitted by the analyst engine when a match is identified as a final or
// knockout. Distinct from `motivation_state` because pressure derives from
// the STAGE of the competition (final / semifinal / playoff), not from how
// much each side has on the line.
export const PRESSURE_STATES = {
  FINAL: {
    icon: 'Trophy',
    tone: 'cyan',
    label_es: 'Final',
    label_en: 'Final',
    hint_es: 'Final del torneo: motivación máxima en ambos lados; vigilar volatilidad de presión.',
    hint_en: 'Tournament final: maximum motivation on both sides; watch pressure volatility.',
  },
  KNOCKOUT_HIGH_PRESSURE: {
    icon: 'Flame',
    tone: 'amber',
    label_es: 'Eliminatoria',
    label_en: 'Knockout',
    hint_es: 'Eliminatoria de alta presión: ambos equipos juegan supervivencia.',
    hint_en: 'High-pressure knockout: both teams play for survival.',
  },
  LEAGUE_URGENCY: {
    icon: 'TrendingUp',
    tone: 'amber',
    label_es: 'Urgencia de liga',
    label_en: 'League urgency',
    hint_es: 'Partido de liga con descenso/título/europa en juego.',
    hint_en: 'League fixture with relegation/title/european qualification at stake.',
  },
  NORMAL_LEAGUE: {
    icon: 'Activity',
    tone: 'emerald',
    label_es: 'Liga normal',
    label_en: 'Normal league',
    hint_es: 'Partido de liga sin presión externa particular.',
    hint_en: 'Standard league fixture without external pressure.',
  },
  LOW_STAKES: {
    icon: 'Clock',
    tone: 'slate',
    label_es: 'Sin riesgo deportivo',
    label_en: 'Low stakes',
    hint_es: 'Ninguno de los dos equipos tiene algo real por jugar.',
    hint_en: 'Neither side has anything real on the line.',
  },
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


// ───────────────────────────────────────────────────────────────────────────
// Historical Learning v2 — derives a decision-support signal from /learning/stats
// ───────────────────────────────────────────────────────────────────────────
//
// PHILOSOPHY:
//   - Read-only learning. NEVER mutates picks or auto-overrides the LLM score.
//   - Output is a SUGGESTION layer for the user/UI, not an actuator.
//   - Always surfaces low-sample warnings to prevent over-trust on noise.
//
// The signal is consumed by HistoricalPatternBadge + EngineNarrativeBlock.

/**
 * deriveHistoricalSignal(stat, pick)
 *
 * @param {object|null} stat — a single pattern row from /api/learning/stats
 *        e.g. { market, match_state, samples, wins, losses, winrate,
 *               reliability, engine_agreement }
 * @param {object|null} pick — the analyst pick (used for visual_confidence
 *        cross-check; optional).
 * @returns {object} historical_signal
 *
 *   {
 *     status: 'no-data' | 'low-sample' | 'weak' | 'neutral' | 'strong' | 'elite',
 *     pattern_strength: 0..100,                  // composite score
 *     sample_size: number,
 *     winrate: number|null,                      // 0..100
 *     reliability: number,                       // 0..100
 *     engine_agreement: number,                  // 0..100
 *     confidence_modifier_suggestion: -10..+10,  // pure UI hint, never applied
 *     warning_if_low_sample: boolean,
 *     trust_label_es / trust_label_en: short label for the badge tier
 *   }
 */
export function deriveHistoricalSignal(stat, pick = null) {
  if (!stat || typeof stat !== 'object') {
    return {
      status: 'no-data',
      pattern_strength: 0,
      sample_size: 0,
      winrate: null,
      reliability: 0,
      engine_agreement: 0,
      confidence_modifier_suggestion: 0,
      warning_if_low_sample: true,
      trust_label_es: 'Sin datos',
      trust_label_en: 'No data',
    };
  }
  const samples = Number(stat.samples || 0);
  const winrate = stat.winrate == null ? null : Number(stat.winrate);
  const reliability = Number(stat.reliability || 0);
  const engineAgreement = Number(stat.engine_agreement || 0);

  // ── Pattern strength composite (0..100) ──
  // 50% reliability + 30% engine_agreement + 20% sample-coverage (capped at 30).
  const sampleCoverage = Math.min(100, (samples / 30) * 100);
  const patternStrength = Math.round(
    0.50 * reliability + 0.30 * engineAgreement + 0.20 * sampleCoverage
  );

  // ── Status tier ──
  let status;
  if (samples <= 2) status = 'low-sample';
  else if (patternStrength >= 60) status = 'elite';
  else if (patternStrength >= 40) status = 'strong';
  else if (patternStrength >= 22) status = 'neutral';
  else status = 'weak';

  // ── Confidence modifier suggestion (display-only) ──
  // Caps at ±10 to prevent any temptation of auto-overriding the LLM.
  // Requires BOTH meaningful reliability AND meaningful sample size.
  let modifier = 0;
  if (samples >= 5 && winrate != null) {
    if (winrate >= 70 && reliability >= 20) modifier = +6;
    else if (winrate >= 60 && reliability >= 15) modifier = +3;
    else if (winrate <= 35) modifier = -6;
    else if (winrate <= 45) modifier = -3;
  }
  // High engine agreement adds a small extra nudge in the same direction
  if (modifier > 0 && engineAgreement >= 50) modifier = Math.min(10, modifier + 2);
  if (modifier < 0 && engineAgreement >= 50) modifier = Math.max(-10, modifier - 2);

  // ── Cross-check vs visual confidence (don't change the value, just warn) ──
  // Used by detectContradictions(); we just expose flags here.
  const visualConf = Number(pick?.recommendation?.confidence_score) || 0;
  const visualHighButPatternWeak = (
    visualConf >= 70 && (status === 'weak' || status === 'low-sample')
  );

  const trustLabels = {
    'no-data':    { es: 'Sin datos',          en: 'No data' },
    'low-sample': { es: 'Muestra insuficiente', en: 'Low sample' },
    weak:         { es: 'Patrón débil',       en: 'Weak pattern' },
    neutral:      { es: 'Patrón neutro',      en: 'Neutral pattern' },
    strong:       { es: 'Patrón fuerte',      en: 'Strong pattern' },
    elite:        { es: 'Patrón élite',       en: 'Elite pattern' },
  };

  return {
    status,
    pattern_strength: Math.max(0, Math.min(100, patternStrength)),
    sample_size: samples,
    winrate,
    reliability: Math.round(reliability * 10) / 10,
    engine_agreement: Math.round(engineAgreement * 10) / 10,
    confidence_modifier_suggestion: modifier,
    warning_if_low_sample: samples <= 4,
    visual_high_pattern_weak: visualHighButPatternWeak,
    trust_label_es: trustLabels[status].es,
    trust_label_en: trustLabels[status].en,
  };
}


// ───────────────────────────────────────────────────────────────────────────
// Contradiction Detection v1 — surfaces conflicts between independent signals
// ───────────────────────────────────────────────────────────────────────────
//
// Returns a list of contradictions, each with severity {info|warn|critical}.
// Each contradiction is fully translatable and self-explanatory in the UI.
//
// We DO NOT auto-discard picks based on these — we just surface them so the
// user can override their own bias. The engine remains the analyst, not the
// arbitrator.

const OVER_MARKET_RE = /(^|\s)(over|btts|goleador|total\s+over|gg)/i;

export function detectContradictions(pick, intel, historicalSignal = null) {
  if (!pick || !intel) return [];
  const out = [];
  const rec = pick.recommendation || {};
  const market = String(rec.market || '');
  const conf = Number(rec.confidence_score) || 0;

  // 1) High confidence + high volatility → unstable conviction
  if (conf >= 70 && intel.volatility >= 55) {
    out.push({
      id: 'high-conf-high-volatility',
      severity: 'warn',
      key: 'high-conf-high-volatility',
      title_es: 'Alta confianza con alta volatilidad',
      title_en: 'High confidence with high volatility',
      detail_es: `Confianza ${conf} sobre un escenario con volatilidad ${intel.volatility}. Las señales son contradictorias — el upside puede colapsar rápido.`,
      detail_en: `Confidence ${conf} on a scenario with volatility ${intel.volatility}. Signals conflict — the upside may collapse fast.`,
    });
  }

  // 2) Over market in a CONTROLLED_MATCH (or LOW_URGENCY) → market mismatch
  if (OVER_MARKET_RE.test(market) && (intel.matchState === 'CONTROLLED_MATCH' || intel.matchState === 'LOW_URGENCY')) {
    out.push({
      id: 'over-in-controlled',
      severity: 'critical',
      key: 'over-in-controlled',
      title_es: 'Mercado Over en partido controlado',
      title_en: 'Over market in a controlled match',
      detail_es: 'El motor recomienda un mercado de goles altos en un escenario que estadísticamente tiende a producir pocos goles. Considerar mercados Under / Doble Oportunidad.',
      detail_en: 'The engine suggests a high-goals market in a scenario that statistically trends low. Consider Under / Double Chance markets instead.',
    });
  }

  // 3) Low sample historical pattern + high visual confidence
  if (historicalSignal && historicalSignal.visual_high_pattern_weak) {
    out.push({
      id: 'low-sample-high-conf',
      severity: 'warn',
      key: 'low-sample-high-conf',
      title_es: 'Confianza alta sin respaldo histórico',
      title_en: 'High confidence without historical backing',
      detail_es: `Confianza visual ${conf} pero el patrón histórico (${market} / ${intel.matchState}) tiene solo ${historicalSignal.sample_size} muestras o reliability baja. Tratar el pick como exploratorio.`,
      detail_en: `Visual confidence ${conf} but the historical pattern (${market} / ${intel.matchState}) has only ${historicalSignal.sample_size} samples or low reliability. Treat the pick as exploratory.`,
    });
  }

  // 4) High fragility + protected-market boast → questionable safety claim
  if (intel.fragility >= 55 && PROTECTED_MARKETS.some((m) => market.toLowerCase().includes(m.toLowerCase()))) {
    out.push({
      id: 'fragile-protected',
      severity: 'info',
      key: 'fragile-protected',
      title_es: 'Mercado "protegido" con alta fragilidad',
      title_en: 'Protected market under high fragility',
      detail_es: `${market} suele ser estable, pero los datos actuales muestran fragilidad ${intel.fragility}. Validar bajas/odds antes de apostar.`,
      detail_en: `${market} is usually stable, but current data shows fragility ${intel.fragility}. Validate absences/odds before betting.`,
    });
  }

  // 5) Negative drivers dominate while confidence is high
  const negCount = (intel.drivers || []).filter((d) => d.sign === 'negative').length;
  const posCount = (intel.drivers || []).filter((d) => d.sign === 'positive').length;
  if (conf >= 70 && negCount > posCount && negCount >= 2) {
    out.push({
      id: 'negative-drivers-dominant',
      severity: 'warn',
      key: 'negative-drivers-dominant',
      title_es: 'Drivers negativos dominan',
      title_en: 'Negative drivers dominate',
      detail_es: `${negCount} drivers negativos vs ${posCount} positivos, aún con confianza ${conf}. La narrativa del motor puede estar sobreponderando un factor único.`,
      detail_en: `${negCount} negative vs ${posCount} positive drivers, yet confidence is ${conf}. The engine's narrative may be over-weighting a single factor.`,
    });
  }

  // 6) Stale data + confidence ≥ 70 → freshness risk
  const fresh = pick.data_freshness || {};
  if (conf >= 70 && (String(fresh.odds || '').toLowerCase() === 'stale' || String(fresh.context || '').toLowerCase() === 'stale')) {
    out.push({
      id: 'stale-high-conf',
      severity: 'info',
      key: 'stale-high-conf',
      title_es: 'Confianza alta con datos viejos',
      title_en: 'High confidence on stale data',
      detail_es: 'Los snapshots de odds o contexto están marcados como stale. Re-validar antes de tomar acción.',
      detail_en: 'Odds or context snapshots are stale. Re-validate before acting.',
    });
  }

  return out;
}


// ───────────────────────────────────────────────────────────────────────────
// Engine Narrative — assembles short professional sentences for the UI
// ───────────────────────────────────────────────────────────────────────────
//
// Returns three buckets: says (engine endorses), avoids (engine recommends NOT
// touching), cautious (engine is conditionally positive but flagging risks).
// All strings are localized and grounded on the derived intel + contradictions.

export function buildEngineNarrative(pick, intel, historicalSignal, contradictions = []) {
  if (!pick || !intel) return { says: [], avoids: [], cautious: [] };
  const rec = pick.recommendation || {};
  const market = rec.market || '';
  const conf = Number(rec.confidence_score) || 0;
  const ms = MATCH_STATES[intel.matchState] || MATCH_STATES.CONTROLLED_MATCH;

  const says = [];
  const avoids = [];
  const cautious = [];

  // SAYS (positive endorsement)
  if (conf >= 60 && market) {
    says.push({
      es: `Recomienda "${market}" con confianza ${conf} en escenario ${(ms.label_es || '').toLowerCase()}.`,
      en: `Recommends "${market}" with confidence ${conf} in a ${(ms.label_en || '').toLowerCase()} scenario.`,
    });
  }
  if (intel.bestFor && intel.bestFor.length > 0) {
    says.push({
      es: `Mercados con mejor ajuste contextual: ${intel.bestFor.slice(0, 3).join(', ')}.`,
      en: `Best-fitting protected markets: ${intel.bestFor.slice(0, 3).join(', ')}.`,
    });
  }
  if (historicalSignal && (historicalSignal.status === 'strong' || historicalSignal.status === 'elite')) {
    const w = historicalSignal.winrate;
    says.push({
      es: `Patrón histórico ${historicalSignal.status === 'elite' ? 'élite' : 'fuerte'}: ${market} en ${intel.matchState} ganó ${w}% (n=${historicalSignal.sample_size}).`,
      en: `${historicalSignal.status === 'elite' ? 'Elite' : 'Strong'} historical pattern: ${market} in ${intel.matchState} won ${w}% (n=${historicalSignal.sample_size}).`,
    });
  }

  // AVOIDS (negative guidance)
  if (intel.avoid && intel.avoid.length > 0) {
    avoids.push({
      es: `Evitar mercados frágiles: ${intel.avoid.slice(0, 3).join(', ')}.`,
      en: `Avoid fragile markets: ${intel.avoid.slice(0, 3).join(', ')}.`,
    });
  }
  if (intel.fragility >= 60) {
    avoids.push({
      es: 'Mantener stake bajo o pasar: fragilidad sobre el umbral seguro.',
      en: 'Keep stake low or pass: fragility above the safe threshold.',
    });
  }
  contradictions
    .filter((c) => c.severity === 'critical')
    .forEach((c) => {
      avoids.push({ es: c.title_es, en: c.title_en });
    });

  // CAUTIOUS (conditional positives)
  if (intel.volatility >= 40 && intel.volatility < 60) {
    cautious.push({
      es: `Volatilidad media (${intel.volatility}): vigilar movimiento de cuotas las próximas horas.`,
      en: `Medium volatility (${intel.volatility}): monitor odds movement over the next hours.`,
    });
  }
  if (historicalSignal && historicalSignal.warning_if_low_sample && historicalSignal.status !== 'no-data') {
    cautious.push({
      es: `Patrón aún con muestra pequeña (n=${historicalSignal.sample_size}): tratar como observacional.`,
      en: `Pattern sample still small (n=${historicalSignal.sample_size}): treat as observational.`,
    });
  }
  if ((pick.risks || []).length > 0) {
    cautious.push({
      es: `${pick.risks.length} bandera${pick.risks.length > 1 ? 's' : ''} de riesgo detectada${pick.risks.length > 1 ? 's' : ''} por el analista.`,
      en: `${pick.risks.length} analyst risk flag${pick.risks.length > 1 ? 's' : ''} detected.`,
    });
  }
  contradictions
    .filter((c) => c.severity !== 'critical')
    .forEach((c) => {
      cautious.push({ es: c.title_es, en: c.title_en });
    });

  return { says, avoids, cautious };
}



/**
 * deriveConfidenceBreakdown(pick) — Confidence Explanation Tree
 *
 * Decomposes the LLM's `confidence_score` into signed contributing factors
 * (motivation, form, home advantage, injuries, volatility, freshness, market).
 *
 * Returns:
 *   {
 *     baseline: 50,                  // engine neutral starting point
 *     items: [
 *       { key: 'motivation', delta: +18, detail_es, detail_en },
 *       { key: 'form',       delta: +12, ... },
 *       ...
 *     ],
 *     computed_total: 84,            // baseline + sum(items.delta), clamped 0..100
 *     reported: 76,                  // pick.recommendation.confidence_score
 *     gap: -8                        // difference (computed_total - reported)
 *   }
 *
 * The gap is shown to users as "Engine reconciliation" — if positive, our
 * derived factors are MORE optimistic than the LLM (LLM saw a hidden risk);
 * if negative, LLM was MORE optimistic than the explainability sums.
 *
 * IMPORTANT: This is an EXPLANATION layer, NOT a re-scoring layer. The
 * confidence_score from the LLM remains the source of truth; this UI only
 * tries to attribute pieces of it.
 */
export function deriveConfidenceBreakdown(pick) {
  if (!pick) return null;
  const motHome = pick.motivation?.home?.level ?? 3;
  const motAway = pick.motivation?.away?.level ?? 3;
  const key = pick.key_data || {};
  const rec = pick.recommendation || {};
  const freshness = pick.data_freshness || {};
  const items = [];

  // ── Motivation: +/− based on average and gap ─────────────────────────────
  const motAvg = (motHome + motAway) / 2;
  const motDelta = Math.round((motAvg - 3) * 9); // +18 if avg=5, -18 if avg=1
  items.push({
    key: 'motivation',
    delta: motDelta,
    detail_es: `Promedio motivacional ${motAvg.toFixed(1)}/5 → ${motDelta >= 0 ? '+' : ''}${motDelta}`,
    detail_en: `Avg motivation ${motAvg.toFixed(1)}/5 → ${motDelta >= 0 ? '+' : ''}${motDelta}`,
  });

  // ── Form: ratio of W vs L across both teams ──────────────────────────────
  const allForm = `${key.form_home || ''}${key.form_away || ''}`;
  if (allForm) {
    const W = (allForm.match(/W/g) || []).length;
    const L = (allForm.match(/L/g) || []).length;
    const formDelta = (W - L) * 2; // 5W vs 5L = ±10, capped naturally
    items.push({
      key: 'form',
      delta: formDelta,
      detail_es: `Forma combinada ${W}W − ${L}L → ${formDelta >= 0 ? '+' : ''}${formDelta}`,
      detail_en: `Combined form ${W}W − ${L}L → ${formDelta >= 0 ? '+' : ''}${formDelta}`,
    });
  }

  // ── Home advantage: small constant boost (+6) when home motivation ≥ away
  if (motHome >= motAway) {
    items.push({
      key: 'home-advantage',
      delta: 6,
      detail_es: 'Localía aporta +6 en mercados protegidos.',
      detail_en: 'Home edge contributes +6 in protected markets.',
    });
  }

  // ── Injuries: −2 per absence (cap −12)
  const injH = key.injuries_home ?? 0;
  const injA = key.injuries_away ?? 0;
  if (injH || injA) {
    const injDelta = -Math.min(12, (injH + injA) * 2);
    items.push({
      key: 'absences',
      delta: injDelta,
      detail_es: `Bajas (local ${injH} / visita ${injA}) → ${injDelta}`,
      detail_en: `Absences (home ${injH} / away ${injA}) → ${injDelta}`,
    });
  }

  // ── Volatility / staleness penalties from freshness flags
  let volPenalty = 0;
  const odds = (freshness.odds || '').toLowerCase();
  const ctx = (freshness.context || '').toLowerCase();
  if (odds === 'stale') volPenalty -= 5;
  if (ctx === 'stale') volPenalty -= 3;
  if (!key.odds_1x2?.home) volPenalty -= 4;
  if ((key.line_movement || 'desconocido').toLowerCase() === 'desconocido') volPenalty -= 3;
  if (volPenalty < 0) {
    items.push({
      key: 'volatility',
      delta: volPenalty,
      detail_es: 'Penalización por datos viejos / pocas señales de mercado.',
      detail_en: 'Penalty for stale data / sparse market signals.',
    });
  }

  // ── Risk flags from the analyst itself: each risk = -3
  const riskCount = (pick.risks || []).length;
  if (riskCount > 0) {
    const r = -riskCount * 3;
    items.push({
      key: 'risk-flags',
      delta: r,
      detail_es: `${riskCount} bandera${riskCount > 1 ? 's' : ''} de riesgo del motor.`,
      detail_en: `${riskCount} engine risk flag${riskCount > 1 ? 's' : ''}.`,
    });
  }

  const baseline = 50;
  const sum = items.reduce((acc, it) => acc + (it.delta || 0), 0);
  const computed_total = Math.max(0, Math.min(100, baseline + sum));
  const reported = Math.max(0, Math.min(100, Number(rec.confidence_score) || 0));

  return {
    baseline,
    items,
    computed_total,
    reported,
    gap: reported - computed_total,
  };
}

