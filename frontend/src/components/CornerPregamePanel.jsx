import { useState } from 'react';
import {
  Flag, ShieldCheck, ShieldAlert, AlertTriangle, Calculator, Activity,
} from 'lucide-react';

/**
 * CornerPregamePanel — dedicated visual panel for pre-match corner picks
 * surfaced by `corner_market_layer._pregame_protected_recommendation`.
 *
 * Renders ONLY when the rescued pick was produced in pregame mode (no
 * automatic corner odds available yet). Mirrors the visual cadence of
 * ExplosiveInningRiskPanel (badge, headline, signal list, paste-odds
 * calculator) but uses football-corner specific data:
 *
 *   • per-team corner averages (≥ 7 partidos)
 *   • combined projected total
 *   • data_quality badge (strong / usable / thin / insufficient)
 *   • trap signals (SLOW_POSSESSION_LOW_DEPTH, EARLY_SCORING_FAVOURITE,
 *     ONE_SIDED_PRESSURE)
 *   • suggested line + side with conservative protected margin
 *
 * The user can paste their bookmaker odds in-place to compute EV
 * exactly like the manual-review flow.
 */

const QUALITY_META = {
  strong: {
    icon:       ShieldCheck,
    label:      'STRONG',
    headline:   '10+ partidos con datos consistentes',
    wrapper:    'border-emerald-500/30 bg-emerald-500/[0.05]',
    iconColor:  'text-emerald-300',
    badge:      'border-emerald-500/45 bg-emerald-500/15 text-emerald-200',
    headingClr: 'text-emerald-200',
    dot:        'bg-emerald-400',
  },
  usable: {
    icon:       Flag,
    label:      'USABLE',
    headline:   '7+ partidos — muestra confiable',
    wrapper:    'border-sky-500/30 bg-sky-500/[0.05]',
    iconColor:  'text-sky-300',
    badge:      'border-sky-500/45 bg-sky-500/15 text-sky-200',
    headingClr: 'text-sky-200',
    dot:        'bg-sky-400',
  },
  thin: {
    icon:       AlertTriangle,
    label:      'THIN',
    headline:   'Muestra <7 partidos — manejar con cautela',
    wrapper:    'border-amber-500/30 bg-amber-500/[0.05]',
    iconColor:  'text-amber-300',
    badge:      'border-amber-500/45 bg-amber-500/15 text-amber-200',
    headingClr: 'text-amber-200',
    dot:        'bg-amber-400',
  },
  insufficient: {
    icon:       ShieldAlert,
    label:      'INSUFFICIENT',
    headline:   'Datos insuficientes — pick descartado',
    wrapper:    'border-rose-500/30 bg-rose-500/[0.05]',
    iconColor:  'text-rose-300',
    badge:      'border-rose-500/45 bg-rose-500/15 text-rose-200',
    headingClr: 'text-rose-200',
    dot:        'bg-rose-400',
  },
};

// Spanish labels for trap codes emitted by `detect_corner_trap_signals`.
const TRAP_LABELS_ES = {
  SLOW_POSSESSION_LOW_DEPTH: (
    'Ambos equipos con posesión lenta y poca profundidad — '
    + 'córners proyectados no se materializan.'
  ),
  EARLY_SCORING_FAVOURITE: (
    'Favorito muy corto que suele marcar temprano y controlar el ritmo — '
    + 'partido se abre y bajan los córners.'
  ),
  ONE_SIDED_PRESSURE: (
    'Asimetría marcada en generación de córners — un equipo concentra casi todos.'
  ),
};

function asNumber(v) {
  if (v === null || v === undefined) return null;
  const n = typeof v === 'number' ? v : parseFloat(v);
  return Number.isFinite(n) ? n : null;
}

/** EV = prob*(odds-1) - (1-prob), expressed as percentage. */
function computeEV(decimalOdds, probabilityPct) {
  const o = parseFloat(decimalOdds);
  const p = parseFloat(probabilityPct);
  if (!Number.isFinite(o) || o <= 1 || !Number.isFinite(p) || p <= 0 || p >= 100) return null;
  const prob = p / 100;
  const ev = (prob * (o - 1)) - (1 - prob);
  return {
    edgePct:        ev * 100,
    impliedProbPct: (1 / o) * 100,
  };
}

export function CornerPregamePanel({ item, idx = 0, testId }) {
  // Hook must be declared at the top of the component (rules-of-hooks).
  const [manualOdds, setManualOdds] = useState('');

  const corner = item?.corner_form || item?._corner_form;
  if (!corner || corner.mode !== 'pregame') return null;

  const quality = (corner.data_quality || 'insufficient').toLowerCase();
  const meta = QUALITY_META[quality] || QUALITY_META.insufficient;
  const Icon = meta.icon;

  const expectedTotal = asNumber(corner.expected_total_corners);
  const homeExpected  = asNumber(corner.expected_home_corners);
  const awayExpected  = asNumber(corner.expected_away_corners);
  const homeAvg       = asNumber((corner.home || {}).corners_for_avg);
  const awayAvg       = asNumber((corner.away || {}).corners_for_avg);
  const homeAgainst   = asNumber((corner.home || {}).corners_against_avg);
  const awayAgainst   = asNumber((corner.away || {}).corners_against_avg);
  const homeN         = (corner.home || {}).sample_size || 0;
  const awayN         = (corner.away || {}).sample_size || 0;
  const overRate95Home = asNumber((corner.home || {}).over_9_5_rate);
  const overRate95Away = asNumber((corner.away || {}).over_9_5_rate);

  const trapSignals = Array.isArray(corner.trap_signals) ? corner.trap_signals : [];
  const recommendedLine = asNumber(item.line);
  const recommendedSide = (item.side || '').toUpperCase();
  const confidence = asNumber(item.confidence);

  // Cover probability proxy for the EV calculator:
  // use the empirical rate of the suggested side at the suggested line
  // when available; otherwise fall back to a confidence-scaled prior.
  const coverProbPct = (() => {
    if (recommendedLine === null || !recommendedSide) {
      return confidence;
    }
    if (recommendedSide === 'OVER') {
      // Use over_X_5 from the spec set the engine emits.
      if (recommendedLine === 7.5) {
        // approximate from over_9_5 (always lower) by adding +12pp prior
        const r = overRate95Home !== null && overRate95Away !== null
          ? ((overRate95Home + overRate95Away) / 2) * 100 + 18
          : confidence;
        return Math.max(40, Math.min(85, r));
      }
      if (recommendedLine === 6.5) {
        const r = overRate95Home !== null && overRate95Away !== null
          ? ((overRate95Home + overRate95Away) / 2) * 100 + 25
          : confidence;
        return Math.max(40, Math.min(88, r));
      }
    } else if (recommendedSide === 'UNDER') {
      // Under 12.5 — high prior because line is well above projection.
      const r = expectedTotal !== null ? 70 : confidence;
      return Math.max(50, Math.min(85, r));
    }
    return confidence;
  })();

  // useState hoisted to the top of the component for rules-of-hooks.
  const evCalc = manualOdds && coverProbPct
    ? computeEV(manualOdds, coverProbPct)
    : null;

  return (
    <div
      className={`rounded-md border ${meta.wrapper} px-3 py-2.5 space-y-3`}
      data-testid={testId || `corner-pregame-panel-${idx}`}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          <Icon className={`h-4 w-4 shrink-0 ${meta.iconColor}`} />
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[10px] uppercase tracking-wider font-semibold text-muted-foreground/80">
                Corner Pregame Analysis
              </span>
              <span
                className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] border ${meta.badge} font-semibold`}
                data-testid={`corner-pregame-quality-${idx}`}
              >
                {meta.label}
              </span>
              {expectedTotal !== null ? (
                <span className="text-[10px] tabular-nums text-muted-foreground/85">
                  proyección {expectedTotal.toFixed(1)} córners
                </span>
              ) : null}
            </div>
            <div className={`text-[12px] mt-0.5 font-medium ${meta.headingClr}`}>
              {meta.headline}
            </div>
          </div>
        </div>
        {recommendedLine !== null && recommendedSide ? (
          <span
            className="shrink-0 text-[11px] px-2 py-0.5 rounded-full border border-violet-500/30 bg-violet-500/15 text-violet-200 font-semibold tabular-nums"
            data-testid={`corner-pregame-line-${idx}`}
          >
            {recommendedSide} {recommendedLine}
          </span>
        ) : null}
      </div>

      {/* Per-team averages — mirrors Sofascore-style readout */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2" data-testid={`corner-pregame-stats-${idx}`}>
        <div className="rounded-md border border-border/40 bg-background/40 px-2 py-1.5">
          <div className="flex items-center gap-1 text-muted-foreground/70 text-[10px]">
            <Activity className="h-3 w-3" /> Local · últ. {homeN}
          </div>
          <div className="font-mono tabular-nums text-foreground/90 text-sm mt-0.5">
            {homeAvg !== null ? homeAvg.toFixed(1) : '—'}
            <span className="text-[10px] text-muted-foreground"> a favor</span>
          </div>
          <div className="font-mono tabular-nums text-[10.5px] text-muted-foreground/80">
            {homeAgainst !== null ? homeAgainst.toFixed(1) : '—'} concedidos
          </div>
        </div>
        <div className="rounded-md border border-border/40 bg-background/40 px-2 py-1.5">
          <div className="flex items-center gap-1 text-muted-foreground/70 text-[10px]">
            <Activity className="h-3 w-3" /> Visitante · últ. {awayN}
          </div>
          <div className="font-mono tabular-nums text-foreground/90 text-sm mt-0.5">
            {awayAvg !== null ? awayAvg.toFixed(1) : '—'}
            <span className="text-[10px] text-muted-foreground"> a favor</span>
          </div>
          <div className="font-mono tabular-nums text-[10.5px] text-muted-foreground/80">
            {awayAgainst !== null ? awayAgainst.toFixed(1) : '—'} concedidos
          </div>
        </div>
        <div className="rounded-md border border-violet-500/25 bg-violet-500/[0.07] px-2 py-1.5">
          <div className="text-[10px] uppercase tracking-wide text-violet-300/90">Proyección</div>
          <div className="font-mono tabular-nums text-violet-200 text-sm mt-0.5">
            {expectedTotal !== null ? expectedTotal.toFixed(1) : '—'}
          </div>
          {homeExpected !== null && awayExpected !== null ? (
            <div className="font-mono tabular-nums text-[10.5px] text-muted-foreground/80">
              {homeExpected.toFixed(1)} + {awayExpected.toFixed(1)}
            </div>
          ) : null}
        </div>
        <div className="rounded-md border border-border/40 bg-background/40 px-2 py-1.5">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground/80">Tasa Over 9.5</div>
          <div className="font-mono tabular-nums text-foreground/90 text-sm mt-0.5">
            {overRate95Home !== null && overRate95Away !== null
              ? `${((overRate95Home + overRate95Away) / 2 * 100).toFixed(0)}%`
              : '—'}
          </div>
          <div className="font-mono tabular-nums text-[10.5px] text-muted-foreground/80">
            confianza {confidence ?? '—'}/100
          </div>
        </div>
      </div>

      {/* Trap signals */}
      {trapSignals.length > 0 ? (
        <div>
          <div className="text-[10px] uppercase tracking-wide text-amber-300/90 font-semibold mb-1.5 flex items-center gap-1.5">
            <AlertTriangle className="h-3 w-3" />
            Señales trampa detectadas
          </div>
          <ul className="space-y-1" data-testid={`corner-pregame-traps-${idx}`}>
            {trapSignals.map((t, i) => {
              const severity = (t.severity || '').toLowerCase();
              const sevClass = severity === 'high'
                ? 'bg-rose-500/15 border-rose-500/40 text-rose-200'
                : 'bg-amber-500/15 border-amber-500/40 text-amber-200';
              return (
                <li
                  key={t.code || i}
                  className="flex items-start gap-1.5 text-[11px]"
                  data-testid={`corner-pregame-trap-${idx}-${t.code || i}`}
                >
                  <span className={`shrink-0 mt-[1px] px-1.5 py-px rounded text-[9px] font-semibold border ${sevClass} uppercase`}>
                    {severity || 'info'}
                  </span>
                  <span className="text-foreground/90 leading-relaxed">
                    {TRAP_LABELS_ES[t.code] || t.explanation || t.code}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      {/* Manual odds calculator — same UX as the manual-review row */}
      <div className="rounded-md border border-border/60 bg-background/30 px-3 py-2 space-y-2" data-testid={`corner-pregame-odds-calc-${idx}`}>
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <label className="text-[11px] text-muted-foreground flex items-center gap-1.5" htmlFor={`corner-odds-${idx}`}>
            <Calculator className="h-3 w-3" />
            Pega tu cuota decimal para {recommendedSide} {recommendedLine}:
          </label>
          <input
            id={`corner-odds-${idx}`}
            type="number"
            step="0.01"
            min="1.01"
            placeholder="1.85"
            value={manualOdds}
            onChange={(e) => setManualOdds(e.target.value)}
            className="w-24 text-[12px] tabular-nums bg-background border border-border rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-violet-500/40"
            data-testid={`corner-pregame-odds-input-${idx}`}
          />
        </div>
        {evCalc ? (
          <div
            className={`rounded border px-2 py-1.5 text-[11px] grid grid-cols-3 gap-2 tabular-nums ${evCalc.edgePct >= 0 ? 'border-emerald-500/30 bg-emerald-500/[0.06]' : 'border-rose-500/30 bg-rose-500/[0.05]'}`}
            data-testid={`corner-pregame-ev-${idx}`}
          >
            <div>
              <div className="text-muted-foreground/70 text-[10px]">EV</div>
              <div className={`font-semibold ${evCalc.edgePct >= 0 ? 'text-emerald-200' : 'text-rose-300'}`}>
                {evCalc.edgePct >= 0 ? '+' : ''}{evCalc.edgePct.toFixed(2)}%
              </div>
            </div>
            <div>
              <div className="text-muted-foreground/70 text-[10px]">Implied</div>
              <div className="text-foreground/80">{evCalc.impliedProbPct.toFixed(1)}%</div>
            </div>
            <div>
              <div className="text-muted-foreground/70 text-[10px]">Modelo</div>
              <div className="text-foreground/80">{coverProbPct?.toFixed(1)}%</div>
            </div>
          </div>
        ) : manualOdds ? (
          <div className="text-[10px] text-rose-300/80">Cuota debe ser &gt; 1.01.</div>
        ) : null}
      </div>
    </div>
  );
}

export default CornerPregamePanel;
