/**
 * UnderHiddenRisksCard — Sprint D12 (UI)
 *
 * Render del bloque "Riesgos ocultos del Under" en el MLB pickcard.
 * Consume `total_risk_overlay` producido por el orquestador MLB
 * (mlb_total_risk_overlay.compute_total_risk_overlay), y muestra:
 *
 *   - Header con veredicto (ALLOW/WARN/AVOID/BLOCK) + tail risk.
 *   - editorial_summary (frase corta del overlay).
 *   - 6 tarjetas (grid 2x3) — los 6 pilares de riesgo bajo el Under:
 *       1. Volatilidad starter
 *       2. Riesgo 1ª entrada
 *       3. Ofensiva reciente
 *       4. Explosividad lineup
 *       5. Estrés bullpen
 *       6. Riesgo dominó
 *   - Lista de reason_codes (traducidos al español).
 *
 * Constraints:
 *   - Sólo se muestra cuando overlay.verdict no es "ALLOW" o cuando el
 *     componente recibe explícitamente datos relevantes (no rinde nada
 *     si total_risk_overlay no existe).
 *   - Read-only / observe-only: NO modifica el pick.
 *   - Todos los nodos críticos llevan data-testid kebab-case.
 */
import { AlertTriangle, Flame, Activity, Users, Gauge, Zap, ShieldOff } from 'lucide-react';

const VERDICT_LABEL = {
  ALLOW: 'Permitido',
  WARN:  'Precaución',
  AVOID: 'Evitar',
  BLOCK: 'Bloqueado',
};

const VERDICT_TONE = {
  ALLOW: 'bg-emerald-500/15 text-emerald-100 border-emerald-500/40',
  WARN:  'bg-amber-500/15 text-amber-100 border-amber-500/40',
  AVOID: 'bg-orange-500/20 text-orange-100 border-orange-500/45',
  BLOCK: 'bg-rose-500/20 text-rose-100 border-rose-500/50',
};

const TAIL_LABEL = {
  LOW:     'Cola baja',
  MEDIUM:  'Cola media',
  HIGH:    'Cola alta',
  EXTREME: 'Cola extrema',
};

const TAIL_TONE = {
  LOW:     'bg-emerald-500/10 text-emerald-200 border-emerald-500/30',
  MEDIUM:  'bg-amber-500/10 text-amber-200 border-amber-500/30',
  HIGH:    'bg-orange-500/15 text-orange-200 border-orange-500/35',
  EXTREME: 'bg-rose-500/15 text-rose-200 border-rose-500/40',
};

// Buckets per pillar → tone + label español.
const BUCKET_LABEL_ES = {
  // starter_volatility / first_inning / bullpen_stress: LOW/MEDIUM/HIGH/EXTREME
  LOW:       'Bajo',
  MEDIUM:    'Medio',
  HIGH:      'Alto',
  EXTREME:   'Extremo',
  // recent_offensive_quality: COLD/NEUTRAL/HOT/EXPLOSIVE
  COLD:      'Fría',
  NEUTRAL:   'Neutral',
  HOT:       'Caliente',
  EXPLOSIVE: 'Explosiva',
  // lineup_explosiveness: LOW/AVG/STRONG/EXPLOSIVE
  AVG:       'Promedio',
  STRONG:    'Fuerte',
  // bullpen_stress: FRESH/NORMAL/TIRED/EXHAUSTED
  FRESH:     'Fresco',
  NORMAL:    'Normal',
  TIRED:     'Cansado',
  EXHAUSTED: 'Agotado',
  UNKNOWN:   'Sin datos',
};

function bucketTone(bucket) {
  const b = String(bucket || '').toUpperCase();
  if (['EXTREME', 'EXPLOSIVE', 'EXHAUSTED'].includes(b)) return 'rose';
  if (['HIGH', 'HOT', 'TIRED', 'STRONG'].includes(b)) return 'orange';
  if (['MEDIUM', 'NEUTRAL', 'NORMAL', 'AVG'].includes(b)) return 'amber';
  if (['LOW', 'COLD', 'FRESH'].includes(b)) return 'emerald';
  return 'slate';
}

const TONE_BG = {
  emerald: 'bg-emerald-500/10 text-emerald-200 border-emerald-500/30',
  amber:   'bg-amber-500/10 text-amber-200 border-amber-500/30',
  orange:  'bg-orange-500/15 text-orange-200 border-orange-500/35',
  rose:    'bg-rose-500/15 text-rose-200 border-rose-500/40',
  slate:   'bg-slate-500/10 text-slate-200 border-slate-500/30',
};

// Reason codes → traducción / explicación en español.
const REASON_CODE_ES = {
  VOLATILE_STARTER_VS_EXPLOSIVE_LINEUP: 'Abridor volátil enfrenta lineup explosivo',
  EXTREME_FIRST_INNING_COLLAPSE_RISK:   'Riesgo extremo de colapso en la 1ª entrada',
  FIRST_INNING_COLLAPSE_RISK:           'Riesgo de colapso en la 1ª entrada',
  EARLY_COLLAPSE_RISK:                  'Riesgo de colapso temprano',
  BOTH_OFFENSES_HOT:                    'Ambas ofensivas están calientes',
  BULLPEN_EXHAUSTION_RISK:              'Bullpen al borde del agotamiento',
  DOMINO_RISK_STARTER_TO_BULLPEN:       'Riesgo dominó: del abridor al bullpen',
  UNDER_TAIL_RISK_RECALIBRATED:         'Cola del Under recalibrada (probabilidades ensanchadas)',
};

function fmtScore(s) {
  const n = Number(s);
  if (!Number.isFinite(n)) return '—';
  return Math.round(n).toString();
}

/**
 * Extrae el peor (más alto) de los dos lados (home / away) para una
 * métrica numérica dada, retornando además el bucket asociado al peor.
 */
function peakSide(block, scoreKey) {
  if (!block || typeof block !== 'object') return { score: null, bucket: 'UNKNOWN', side: null };
  const sides = ['home', 'away'];
  let best = { score: -Infinity, bucket: 'UNKNOWN', side: null };
  for (const s of sides) {
    const sb = block[s];
    if (!sb || typeof sb !== 'object') continue;
    const v = Number(sb[scoreKey]);
    if (!Number.isFinite(v)) continue;
    if (v > best.score) {
      best = { score: v, bucket: String(sb.bucket || 'UNKNOWN').toUpperCase(), side: s };
    }
  }
  if (!Number.isFinite(best.score)) {
    return { score: null, bucket: 'UNKNOWN', side: null };
  }
  return best;
}

/**
 * Para `recent_offensive_quality` queremos el bucket "más caliente"
 * de los dos. Mapeo ordinal: COLD=0, NEUTRAL=1, HOT=2, EXPLOSIVE=3.
 */
function peakOffensiveQuality(block) {
  if (!block || typeof block !== 'object') return { bucket: 'UNKNOWN', side: null };
  const order = { COLD: 0, NEUTRAL: 1, HOT: 2, EXPLOSIVE: 3 };
  let best = { rank: -1, bucket: 'UNKNOWN', side: null };
  for (const s of ['home', 'away']) {
    const sb = block[s];
    if (!sb || typeof sb !== 'object') continue;
    const b = String(sb.bucket || 'UNKNOWN').toUpperCase();
    const r = order[b];
    if (r !== undefined && r > best.rank) {
      best = { rank: r, bucket: b, side: s };
    }
  }
  return { bucket: best.bucket, side: best.side };
}

export function UnderHiddenRisksCard({ totalRiskOverlay, testId = 'under-hidden-risks' }) {
  if (!totalRiskOverlay || typeof totalRiskOverlay !== 'object') return null;

  const verdict = String(totalRiskOverlay.verdict || '').toUpperCase();
  const tail = String(totalRiskOverlay.explosive_tail_risk || 'LOW').toUpperCase();
  const editorial = totalRiskOverlay.editorial_summary || '';
  const reasonCodes = Array.isArray(totalRiskOverlay.reason_codes)
    ? totalRiskOverlay.reason_codes
    : [];
  const dispersion = Number(totalRiskOverlay.dispersion_multiplier);

  const components = totalRiskOverlay.components || {};

  // Pillars data extraction.
  const sv = peakSide(components.starter_volatility, 'starter_volatility_score');
  const fi = peakSide(components.first_inning_collapse, 'first_inning_collapse_score');
  const off = peakOffensiveQuality(components.recent_offensive_quality);
  const le = peakSide(components.lineup_explosiveness, 'lineup_explosiveness_score');
  const bp = peakSide(components.bullpen_stress, 'bullpen_stress_score');
  const dr = peakSide(components.domino_risk, 'domino_risk_score');

  const pillars = [
    {
      key: 'starter-volatility',
      icon: AlertTriangle,
      title: 'Volatilidad del starter',
      score: sv.score,
      bucket: sv.bucket,
      side: sv.side,
    },
    {
      key: 'first-inning',
      icon: Flame,
      title: 'Riesgo 1ª entrada',
      score: fi.score,
      bucket: fi.bucket,
      side: fi.side,
    },
    {
      key: 'recent-offense',
      icon: Activity,
      title: 'Ofensiva reciente',
      score: null,
      bucket: off.bucket,
      side: off.side,
    },
    {
      key: 'lineup-explosiveness',
      icon: Zap,
      title: 'Explosividad lineup',
      score: le.score,
      bucket: le.bucket,
      side: le.side,
    },
    {
      key: 'bullpen-stress',
      icon: Gauge,
      title: 'Estrés del bullpen',
      score: bp.score,
      bucket: bp.bucket,
      side: bp.side,
    },
    {
      key: 'domino-risk',
      icon: Users,
      title: 'Riesgo dominó',
      score: dr.score,
      bucket: dr.bucket,
      side: dr.side,
    },
  ];

  const verdictTone = VERDICT_TONE[verdict] || VERDICT_TONE.ALLOW;
  const tailTone = TAIL_TONE[tail] || TAIL_TONE.LOW;

  // Dedupe reason codes preserving order.
  const seen = new Set();
  const uniqueReasons = [];
  for (const rc of reasonCodes) {
    if (!seen.has(rc)) {
      seen.add(rc);
      uniqueReasons.push(rc);
    }
  }

  return (
    <div
      className="mt-3 rounded-lg border border-rose-500/25 bg-rose-500/[0.04] p-3 space-y-2"
      data-testid={testId}
    >
      {/* Header: title + verdict badge + tail risk badge */}
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-1.5">
          <ShieldOff className="h-4 w-4 text-rose-300" />
          <span className="text-[12px] font-semibold tracking-wide text-rose-100/90">
            Riesgos ocultos del Under
          </span>
        </div>
        <div className="flex items-center gap-1.5 flex-wrap">
          <span
            className={`inline-block px-2 py-0.5 rounded text-[10.5px] font-bold border ${verdictTone}`}
            data-testid={`${testId}-verdict-badge`}
          >
            Veredicto: {VERDICT_LABEL[verdict] || verdict || '—'}
          </span>
          <span
            className={`inline-block px-2 py-0.5 rounded text-[10.5px] font-semibold border ${tailTone}`}
            data-testid={`${testId}-tail-badge`}
          >
            {TAIL_LABEL[tail] || tail}
          </span>
          {Number.isFinite(dispersion) && dispersion > 1.0 ? (
            <span
              className="inline-block px-2 py-0.5 rounded text-[10px] font-semibold border bg-fuchsia-500/15 text-fuchsia-100 border-fuchsia-500/35"
              data-testid={`${testId}-dispersion-badge`}
              title="Multiplicador de dispersión aplicado a la cola NB"
            >
              NB ×{dispersion.toFixed(2)}
            </span>
          ) : null}
        </div>
      </div>

      {/* Editorial summary */}
      {editorial ? (
        <p
          className="text-[11px] text-rose-50/85 leading-snug"
          data-testid={`${testId}-editorial`}
        >
          {editorial}
        </p>
      ) : null}

      {/* 6-card grid (2 cols x 3 rows on md+, 1 col on mobile) */}
      <div
        className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2"
        data-testid={`${testId}-grid`}
      >
        {pillars.map((p) => {
          const tone = bucketTone(p.bucket);
          const toneClass = TONE_BG[tone];
          const Icon = p.icon;
          const bucketEs = BUCKET_LABEL_ES[p.bucket] || (p.bucket === 'UNKNOWN' ? 'Sin datos' : p.bucket);
          const sideLabel = p.side === 'home' ? 'local' : p.side === 'away' ? 'visitante' : null;
          return (
            <div
              key={p.key}
              className={`rounded-md border p-2 space-y-1 ${toneClass}`}
              data-testid={`${testId}-card-${p.key}`}
            >
              <div className="flex items-center gap-1.5">
                <Icon className="h-3.5 w-3.5 opacity-90" />
                <span className="text-[10.5px] font-semibold uppercase tracking-wide opacity-90">
                  {p.title}
                </span>
              </div>
              <div className="flex items-baseline justify-between gap-2">
                <span
                  className="text-[10.5px] font-semibold"
                  data-testid={`${testId}-card-${p.key}-bucket`}
                >
                  {bucketEs}
                </span>
                {p.score !== null && p.score !== undefined ? (
                  <span
                    className="text-[14px] font-bold tabular-nums"
                    data-testid={`${testId}-card-${p.key}-score`}
                  >
                    {fmtScore(p.score)}
                  </span>
                ) : null}
              </div>
              {sideLabel ? (
                <div className="text-[9.5px] opacity-70">
                  Peor lado: <span className="font-medium">{sideLabel}</span>
                </div>
              ) : (
                <div className="text-[9.5px] opacity-50">Sin datos por lado</div>
              )}
            </div>
          );
        })}
      </div>

      {/* Reason codes traducidos */}
      {uniqueReasons.length > 0 ? (
        <div className="pt-1.5 border-t border-rose-500/15 space-y-1">
          <div className="text-[10px] uppercase tracking-wide font-semibold opacity-70 text-rose-100">
            Señales detectadas
          </div>
          <ul
            className="flex flex-wrap gap-1.5"
            data-testid={`${testId}-reason-codes`}
          >
            {uniqueReasons.map((rc) => (
              <li
                key={rc}
                className="inline-block px-1.5 py-0.5 rounded text-[10px] font-medium border bg-rose-500/10 text-rose-50 border-rose-500/30"
                data-testid={`${testId}-reason-${rc.toLowerCase().replace(/_/g, '-')}`}
                title={rc}
              >
                {REASON_CODE_ES[rc] || rc}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export default UnderHiddenRisksCard;
