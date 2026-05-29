import { useState } from 'react';
import { ChevronDown, Target, Activity, Gauge, Link as LinkIcon, AlertTriangle, Shield } from 'lucide-react';

/**
 * MLBScriptPanel — visualises the v2 Margin & Total Script Engine output.
 *
 * Renders the per-pick `_mlb_script_v2` payload produced by
 * /api/mlb/day (and bubbled through /api/analysis/run when sport=baseball):
 *
 *   marginProjection, coverProbability, runLineScore, fragilityScore
 *   bestLine, safeLine, aggressiveLine, recommendedLine, lineSafetyScore
 *   sameGameCorrelation (+ reason), pickType (+ reason)
 *   reasons[], risks[]
 *
 * Constraints:
 *  - **Baseball-only** — caller (MatchCard) must skip rendering for other sports.
 *  - Collapsible, opt-in: hidden by default to keep MatchCard compact.
 *  - Renders nothing when the payload is empty / missing.
 *  - Does NOT modify the UI of basketball or football pickcards.
 */

const PICK_TYPE_LABEL = {
  DOMINANT_FAVORITE_RUN_LINE: 'Run Line dominante',
  SMART_LOW_OVER:             'Over protegido (línea baja)',
  PITCHER_UNDER:              'Under por abridores',
  F5_EDGE:                    'Edge F5 (primeras 5)',
  TEAM_TOTAL_EDGE:            'Team Total',
  SAME_GAME_CORRELATED_PAIR:  'Par mismo juego (correlación)',
  GENERIC:                    'Pick MLB',
};

const PICK_TYPE_TONE = {
  DOMINANT_FAVORITE_RUN_LINE: 'emerald',
  SMART_LOW_OVER:             'amber',
  PITCHER_UNDER:              'sky',
  F5_EDGE:                    'violet',
  TEAM_TOTAL_EDGE:            'rose',
  SAME_GAME_CORRELATED_PAIR:  'cyan',
  GENERIC:                    'slate',
};

const SGC_LABEL = {
  POSITIVE: { es: 'Correlación positiva', tone: 'emerald' },
  NEGATIVE: { es: 'Correlación negativa', tone: 'rose' },
  NEUTRAL:  { es: 'Sin correlación clara', tone: 'slate' },
};

function asNumber(v, fallback = null) {
  if (v === null || v === undefined) return fallback;
  const n = typeof v === 'number' ? v : parseFloat(v);
  return Number.isFinite(n) ? n : fallback;
}

function MetricRow({ icon: Icon, label, value, hint, tone = 'slate', testId }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5" data-testid={testId}>
      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        {Icon ? <Icon className="h-3.5 w-3.5 text-muted-foreground/80" /> : null}
        <span>{label}</span>
      </div>
      <div className="flex items-center gap-2">
        <span className={`text-[12px] font-medium tabular-nums tone-${tone}`}>{value}</span>
        {hint ? <span className="text-[10px] text-muted-foreground/70">{hint}</span> : null}
      </div>
    </div>
  );
}

export function MLBScriptPanel({ scriptV2, parlay, testId }) {
  const [expanded, setExpanded] = useState(false);

  if (!scriptV2 || typeof scriptV2 !== 'object') return null;

  const {
    pickType,
    pickTypeReason,
    marginProjection,
    coverProbability,
    runLineScore,
    fragilityScore,
    expectedRuns,
    bestLine,
    safeLine,
    aggressiveLine,
    recommendedLine,
    lineSafetyScore,
    sameGameCorrelation,
    sameGameCorrelationReason,
    reasons = [],
    risks = [],
  } = scriptV2;

  const hasAnything = Boolean(
    pickType ||
    marginProjection !== undefined ||
    coverProbability !== undefined ||
    recommendedLine ||
    reasons.length || risks.length,
  );
  if (!hasAnything) return null;

  const typeLabel = PICK_TYPE_LABEL[pickType] || PICK_TYPE_LABEL.GENERIC;
  const typeTone  = PICK_TYPE_TONE[pickType]  || 'slate';
  const sgc       = SGC_LABEL[String(sameGameCorrelation || '').toUpperCase()];

  const marginNum  = asNumber(marginProjection);
  const coverNum   = asNumber(coverProbability);
  const rlsNum     = asNumber(runLineScore);
  const erNum      = asNumber(expectedRuns);
  const fragNum    = asNumber(fragilityScore);
  const lineSafeNum= asNumber(lineSafetyScore);

  return (
    <div
      className="rounded-lg border border-emerald-500/20 bg-emerald-500/[0.03] p-3 space-y-2"
      data-testid={testId || 'mlb-script-panel'}
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center justify-between gap-3 text-left group"
        aria-expanded={expanded}
        data-testid={`${testId || 'mlb-script-panel'}-toggle`}
      >
        <div className="flex items-center gap-2 min-w-0 flex-wrap">
          <span className="micro-label">MLB SCRIPT v2</span>
          <span
            className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] border tone-${typeTone}`}
            data-testid={`${testId || 'mlb-script'}-type-badge`}
          >
            <Target className="h-3 w-3" />
            {typeLabel}
          </span>
          {sgc ? (
            <span
              className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] border tone-${sgc.tone}`}
              data-testid={`${testId || 'mlb-script'}-sgc-badge`}
            >
              <LinkIcon className="h-3 w-3" />
              {sgc.es}
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-2 shrink-0 text-[10px] text-muted-foreground">
          {marginNum !== null ? (
            <span className="tabular-nums" data-testid={`${testId || 'mlb-script'}-margin-summary`}>
              +{marginNum.toFixed(2)} marg.
            </span>
          ) : null}
          {coverNum !== null ? (
            <span className="tabular-nums">{coverNum.toFixed(1)}% cover</span>
          ) : null}
          <ChevronDown className={`h-3.5 w-3.5 transition-transform ${expanded ? 'rotate-180' : ''}`} />
        </div>
      </button>

      {expanded && (
        <div className="pt-1 space-y-3" data-testid={`${testId || 'mlb-script-panel'}-body`}>
          {/* Pick type rationale */}
          {pickTypeReason ? (
            <p className="text-[11px] text-muted-foreground leading-relaxed">
              {pickTypeReason}
            </p>
          ) : null}

          {/* Margin & Cover metrics */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1 border-t border-border/40 pt-2">
            {marginNum !== null ? (
              <MetricRow
                icon={Activity}
                label="Projected margin"
                value={`${marginNum >= 0 ? '+' : ''}${marginNum.toFixed(2)}`}
                hint="carreras"
                tone={marginNum >= 1.8 ? 'emerald' : (marginNum >= 1.0 ? 'amber' : 'slate')}
                testId={`${testId || 'mlb-script'}-metric-margin`}
              />
            ) : null}
            {coverNum !== null ? (
              <MetricRow
                icon={Target}
                label="Cover probability"
                value={`${coverNum.toFixed(1)}%`}
                tone={coverNum >= 55 ? 'emerald' : (coverNum >= 45 ? 'amber' : 'slate')}
                testId={`${testId || 'mlb-script'}-metric-cover`}
              />
            ) : null}
            {rlsNum !== null ? (
              <MetricRow
                icon={Gauge}
                label="Run Line score"
                value={`${rlsNum.toFixed(0)}/100`}
                tone={rlsNum >= 72 ? 'emerald' : (rlsNum >= 60 ? 'amber' : 'slate')}
                testId={`${testId || 'mlb-script'}-metric-rl-score`}
              />
            ) : null}
            {fragNum !== null ? (
              <MetricRow
                icon={Shield}
                label="Fragility"
                value={`${fragNum.toFixed(0)}/100`}
                tone={fragNum <= 35 ? 'emerald' : (fragNum <= 55 ? 'amber' : 'rose')}
                testId={`${testId || 'mlb-script'}-metric-fragility`}
              />
            ) : null}
          </div>

          {/* Totals block — best line / safe / aggressive */}
          {(erNum !== null || recommendedLine) && (
            <div className="border-t border-border/40 pt-2 space-y-1.5">
              <div className="micro-label">TOTALES</div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1">
                {erNum !== null ? (
                  <MetricRow
                    label="Expected runs"
                    value={erNum.toFixed(1)}
                    testId={`${testId || 'mlb-script'}-metric-exp-runs`}
                  />
                ) : null}
                {recommendedLine ? (
                  <MetricRow
                    label="Línea recomendada"
                    value={recommendedLine}
                    tone="emerald"
                    testId={`${testId || 'mlb-script'}-metric-recommended`}
                  />
                ) : null}
                {safeLine ? (
                  <MetricRow
                    label="Línea protegida"
                    value={safeLine}
                    tone="sky"
                    testId={`${testId || 'mlb-script'}-metric-safe-line`}
                  />
                ) : null}
                {aggressiveLine ? (
                  <MetricRow
                    label="Línea agresiva"
                    value={aggressiveLine}
                    tone="amber"
                    testId={`${testId || 'mlb-script'}-metric-aggressive`}
                  />
                ) : null}
                {bestLine && bestLine !== recommendedLine ? (
                  <MetricRow
                    label="Best line"
                    value={bestLine}
                    testId={`${testId || 'mlb-script'}-metric-best`}
                  />
                ) : null}
                {lineSafeNum !== null ? (
                  <MetricRow
                    label="Line safety score"
                    value={`${lineSafeNum.toFixed(0)}/100`}
                    tone={lineSafeNum >= 65 ? 'emerald' : (lineSafeNum >= 45 ? 'amber' : 'slate')}
                    testId={`${testId || 'mlb-script'}-metric-line-safety`}
                  />
                ) : null}
              </div>
            </div>
          )}

          {/* Same-game correlation reason */}
          {sgc && sameGameCorrelationReason ? (
            <div className="border-t border-border/40 pt-2">
              <div className="micro-label mb-1">CORRELACIÓN MISMO JUEGO</div>
              <p className={`text-[11px] leading-relaxed tone-${sgc.tone}`}>
                {sameGameCorrelationReason}
              </p>
            </div>
          ) : null}

          {/* Reasons + Risks */}
          {(reasons.length || risks.length) ? (
            <div className="border-t border-border/40 pt-2 grid grid-cols-1 sm:grid-cols-2 gap-3">
              {reasons.length ? (
                <div data-testid={`${testId || 'mlb-script'}-reasons`}>
                  <div className="micro-label mb-1 tone-emerald">A FAVOR</div>
                  <ul className="space-y-1">
                    {reasons.slice(0, 5).map((r, i) => (
                      <li key={i} className="text-[11px] text-muted-foreground leading-snug">
                        • {r}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {risks.length ? (
                <div data-testid={`${testId || 'mlb-script'}-risks`}>
                  <div className="micro-label mb-1 tone-rose">RIESGOS</div>
                  <ul className="space-y-1">
                    {risks.slice(0, 5).map((r, i) => (
                      <li key={i} className="text-[11px] text-muted-foreground leading-snug flex items-start gap-1">
                        <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0 text-rose-500/80" />
                        <span>{r}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : null}

          {/* Parlay context (optional — passed by parent if present) */}
          {parlay && (parlay.whyThisParlayWorks?.length || parlay.whyThisParlayCanFail?.length) ? (
            <div className="border-t border-border/40 pt-2 space-y-2" data-testid={`${testId || 'mlb-script'}-parlay`}>
              <div className="flex items-center gap-2 flex-wrap">
                <span className="micro-label">PARLAY MLB</span>
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] border tone-emerald">
                  {parlay.parlayType || 'MLB_ONLY'}
                </span>
                {typeof parlay.finalParlayScore === 'number' ? (
                  <span className="text-[10px] text-muted-foreground tabular-nums">
                    score {parlay.finalParlayScore}/100
                  </span>
                ) : null}
                {parlay.riskLevel ? (
                  <span className={`text-[10px] tone-${parlay.riskLevel === 'LOW' ? 'emerald' : parlay.riskLevel === 'MEDIUM' ? 'amber' : 'rose'}`}>
                    riesgo {parlay.riskLevel}
                  </span>
                ) : null}
              </div>
              {parlay.whyThisParlayWorks?.length ? (
                <div>
                  <div className="micro-label mb-1 tone-emerald">POR QUÉ FUNCIONA</div>
                  <ul className="space-y-1">
                    {parlay.whyThisParlayWorks.slice(0, 4).map((r, i) => (
                      <li key={i} className="text-[11px] text-muted-foreground leading-snug">• {r}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {parlay.whyThisParlayCanFail?.length ? (
                <div>
                  <div className="micro-label mb-1 tone-rose">POR QUÉ PUEDE FALLAR</div>
                  <ul className="space-y-1">
                    {parlay.whyThisParlayCanFail.slice(0, 4).map((r, i) => (
                      <li key={i} className="text-[11px] text-muted-foreground leading-snug">• {r}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

export default MLBScriptPanel;
