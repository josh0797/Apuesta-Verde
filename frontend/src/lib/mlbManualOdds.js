/**
 * D9.2 — Shared client-side helpers for the MLB manual-odds inputs.
 *
 * The "Lecturas estructurales que requieren cuota" section computes
 * EV / Implied / Modelo locally without round-tripping the backend
 * because it has the engine's `coverProbability` already in the pick
 * payload. The recommended-bet card (`InlineManualOddsInput`) was
 * round-tripping the backend, and when the backend reprice path
 * failed to extract a probability the user saw "no se pudo recalcular
 * porque no se encontró probabilidad estimada para el pick" — even
 * though the same probability was sitting in the pick object that
 * powers the structural panel.
 *
 * Single source of truth, used by both components.
 */

/**
 * Pure EV / Implied calculator.
 *
 * @param {string|number} decimalOdds  Decimal odds (1.91, 2.05, ...).
 *                                      Accepts comma or dot as separator.
 * @param {number} probabilityPct      Model probability in 0..100.
 * @returns {{parsedOdds:number, edgePct:number, impliedProbPct:number,
 *           breakEvenOdds:number|null}|null}
 *          Returns ``null`` when either input is invalid.
 */
export function computeEvFromOdds(decimalOdds, probabilityPct) {
  const oRaw = typeof decimalOdds === 'string'
    ? decimalOdds.trim().replace(',', '.')
    : decimalOdds;
  const o = parseFloat(oRaw);
  const p = parseFloat(probabilityPct);
  if (!Number.isFinite(o) || o <= 1) return null;
  if (!Number.isFinite(p) || p <= 0 || p >= 100) return null;
  const prob = p / 100;
  const ev = (prob * (o - 1)) - (1 - prob);
  return {
    parsedOdds:     o,
    edgePct:        ev * 100,
    impliedProbPct: (1 / o) * 100,
    breakEvenOdds:  prob > 0 ? 1 / prob : null,
  };
}

// ────────────────────────────────────────────────────────────────────
// Model probability extractor — mirrors backend cascade
// ────────────────────────────────────────────────────────────────────
const _toFraction = (v) => {
  const n = parseFloat(v);
  if (!Number.isFinite(n)) return null;
  if (n > 1 && n <= 100) return n / 100;   // looks like a percentage
  if (n >= 0 && n <= 1)  return n;          // already a fraction
  return null;
};

function _marketSide(market, selection) {
  const s = `${market || ''} ${selection || ''}`.toLowerCase();
  if (/\bunder\b|\bu\s*\d|\bmenos\b/.test(s)) return 'under';
  if (/\bover\b|\bo\s*\d|\bmas\b|\bmás\b/.test(s)) return 'over';
  return null;
}

/**
 * Walk a pick object looking for the most reliable model probability
 * in 0..1 range. The cascade matches the backend's
 * ``_extract_model_probability``.
 *
 * @param {object} pick                   Pick / match payload.
 * @param {{market?:string,selection?:string}} ctx
 * @returns {{probability:number, sourcePath:string}|{probability:null}}
 */
export function extractModelProbability(pick, { market, selection } = {}) {
  if (!pick || typeof pick !== 'object') return { probability: null };
  const side = _marketSide(market, selection);

  const probes = [];

  // 1) Top-level probability fields.
  probes.push(['pick.model_probability',  pick.model_probability]);
  probes.push(['pick.cover_probability',  pick.cover_probability]);
  probes.push(['pick.probability',         pick.probability]);

  // 2) key_data block.
  const kd = pick.key_data || {};
  probes.push(['pick.key_data.model_probability', kd.model_probability]);
  probes.push(['pick.key_data.cover_probability', kd.cover_probability]);
  probes.push(['pick.key_data.probability',        kd.probability]);

  // 3) recommendation block.
  const rec = pick.recommendation || {};
  probes.push(['pick.recommendation.model_probability', rec.model_probability]);
  probes.push(['pick.recommendation.cover_probability', rec.cover_probability]);
  probes.push(['pick.recommendation.probability',        rec.probability]);

  // 4) _mlb_script_v2 — generic + side-aware.
  const v2 = pick._mlb_script_v2 || {};
  probes.push(['pick._mlb_script_v2.coverProbability',     v2.coverProbability]);
  probes.push(['pick._mlb_script_v2.estimatedProbability', v2.estimatedProbability]);
  probes.push(['pick._mlb_script_v2.modelProbability',     v2.modelProbability]);
  probes.push(['pick._mlb_script_v2.probability',           v2.probability]);
  if (side === 'under') {
    probes.push(['pick._mlb_script_v2.probabilityUnder', v2.probabilityUnder]);
  } else if (side === 'over') {
    probes.push(['pick._mlb_script_v2.probabilityOver',  v2.probabilityOver]);
  }

  // 5) margin_v2 block.
  const m2 = pick.margin_v2 || {};
  probes.push(['pick.margin_v2.coverProbability',  m2.coverProbability]);
  probes.push(['pick.margin_v2.cover_probability', m2.cover_probability]);
  probes.push(['pick.margin_v2.modelProbability',  m2.modelProbability]);
  probes.push(['pick.margin_v2.probability',        m2.probability]);

  // 6) expected_runs_distribution — side aware.
  const erd = pick.expected_runs_distribution || {};
  if (side === 'under') {
    probes.push(['pick.expected_runs_distribution.probability_under', erd.probability_under]);
    probes.push(['pick.expected_runs_distribution.probabilityUnder',  erd.probabilityUnder]);
    probes.push(['pick.expected_runs_distribution.under_probability', erd.under_probability]);
  } else if (side === 'over') {
    probes.push(['pick.expected_runs_distribution.probability_over', erd.probability_over]);
    probes.push(['pick.expected_runs_distribution.probabilityOver',  erd.probabilityOver]);
    probes.push(['pick.expected_runs_distribution.over_probability', erd.over_probability]);
  }

  // 7) tail_risk — side aware.
  const tr = pick.tail_risk || {};
  if (side === 'under') {
    probes.push(['pick.tail_risk.under_probability', tr.under_probability]);
    probes.push(['pick.tail_risk.probability_under', tr.probability_under]);
  } else if (side === 'over') {
    probes.push(['pick.tail_risk.over_probability', tr.over_probability]);
    probes.push(['pick.tail_risk.probability_over', tr.probability_over]);
  }

  // 8) estimated_probability mirror.
  probes.push(['pick.estimated_probability', pick.estimated_probability]);

  for (const [path, value] of probes) {
    const v = _toFraction(value);
    if (v !== null) return { probability: v, sourcePath: path };
  }
  return { probability: null };
}

/**
 * Convenience: same as :func:`computeEvFromOdds` but pulls the model
 * probability out of the pick itself. Returns the same shape PLUS a
 * ``modelProbabilityPct`` and a ``sourcePath`` for audit / debug.
 */
export function computeEvFromPick(decimalOdds, pick, { market, selection } = {}) {
  const { probability, sourcePath } = extractModelProbability(pick, { market, selection });
  if (probability === null) return null;
  const probPct = probability * 100;
  const ev = computeEvFromOdds(decimalOdds, probPct);
  if (!ev) return null;
  return { ...ev, modelProbabilityPct: probPct, sourcePath };
}
