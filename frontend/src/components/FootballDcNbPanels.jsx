/**
 * Football DC/NB Totals Model + Over Support Panels.
 *
 * Two fail-soft, read-only panels for football picks:
 *   1. FootballTotalsModelPanel   — Poisson vs DC/NB, ρ, NB ratio, mode,
 *                                  market action derived from the
 *                                  recommendation (NEVER recalculated).
 *   2. FootballOverSupportPanel   — Over 1.5 / 2.5 support scores,
 *                                  early-goal 0–30 metrics, recommended
 *                                  Over market.
 *
 * Both panels render `null` when their payload is missing or marked
 * unavailable, so adding them to a MatchCard cannot affect non-football
 * picks or fail-soft fallbacks.
 */

import {
  Sigma,
  Sparkles,
  Clock4,
  TrendingUp,
  Shield,
  AlertTriangle,
  Info,
} from 'lucide-react';

// ─────────────────────────────────────────────────────────────────────
// Format helpers (intentionally NOT recomputing anything — just format)
// ─────────────────────────────────────────────────────────────────────
const pct = (v) => {
  if (v === null || v === undefined) return 'N/D';
  const n = Number(v);
  if (!Number.isFinite(n)) return 'N/D';
  return `${Math.round(n * 100)}%`;
};

const deltaPts = (v) => {
  if (v === null || v === undefined) return 'N/D';
  const n = Number(v);
  if (!Number.isFinite(n)) return 'N/D';
  const sign = n >= 0 ? '+' : '';
  return `${sign}${n.toFixed(1)} pts`;
};

const signedFloat = (v, digits = 3) => {
  if (v === null || v === undefined) return 'N/D';
  const n = Number(v);
  if (!Number.isFinite(n)) return 'N/D';
  const sign = n >= 0 ? '+' : '';
  return `${sign}${n.toFixed(digits)}`;
};

// ─────────────────────────────────────────────────────────────────────
// Shared atoms
// ─────────────────────────────────────────────────────────────────────
const TONE_CLS = {
  emerald: 'bg-emerald-500/15 text-emerald-200 border-emerald-500/30',
  cyan:    'bg-cyan-500/15 text-cyan-200 border-cyan-500/30',
  amber:   'bg-amber-500/15 text-amber-200 border-amber-500/30',
  rose:    'bg-rose-500/15 text-rose-200 border-rose-500/30',
  slate:   'bg-slate-500/15 text-slate-200 border-slate-500/30',
  violet:  'bg-violet-500/15 text-violet-200 border-violet-500/30',
};

function Badge({ tone = 'slate', children, testId, title }) {
  return (
    <span
      data-testid={testId}
      title={title || undefined}
      className={`inline-flex items-center px-1.5 py-0.5 rounded-md border text-[9px] font-semibold uppercase tracking-wide ${TONE_CLS[tone]}`}
    >
      {children}
    </span>
  );
}

function StatRow({ label, value, hint, testId, tone = 'slate' }) {
  if (value === null || value === undefined || value === '') return null;
  return (
    <div className="flex items-center justify-between gap-2" data-testid={testId}>
      <span className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</span>
      <div className="flex items-center gap-1.5">
        <span className={`text-xs font-mono-tabular ${tone === 'emerald' ? 'text-emerald-200' : tone === 'rose' ? 'text-rose-200' : 'text-foreground'}`}>
          {value}
        </span>
        {hint && (
          <span className="text-[9px] text-muted-foreground">{hint}</span>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// 1) FootballTotalsModelPanel
// ─────────────────────────────────────────────────────────────────────
export function FootballTotalsModelPanel({ totalsModel, recommendation, testId }) {
  if (!totalsModel || !totalsModel.available) return null;

  const mode = totalsModel.mode;
  const rho = totalsModel.rho_used;
  const ratio = totalsModel.goals_dispersion_ratio;
  const u25 = totalsModel.under_2_5 || {};
  const u35 = totalsModel.under_3_5 || {};

  const recMarket = (recommendation && (recommendation.market || recommendation.selection)) || null;
  const recMarketLower = String(recMarket || '').toLowerCase();
  const isUnder35 = recMarketLower.includes('under 3.5') || recMarketLower.includes('menos de 3.5');
  const isUnder25 = recMarketLower.includes('under 2.5') || recMarketLower.includes('menos de 2.5');
  const isUnder = isUnder25 || isUnder35 || recMarketLower.includes('under') || recMarketLower.includes('menos');

  // Mode badge
  const modeBadge = (() => {
    if (mode === 'empirical') {
      return (
        <Badge
          tone="emerald"
          testId="totals-model-mode-empirical"
          title="Parámetros calibrados con resultados históricos suficientes."
        >
          EMPIRICAL CALIBRATION
        </Badge>
      );
    }
    if (mode === 'defaults') {
      return (
        <Badge
          tone="slate"
          testId="totals-model-mode-defaults"
          title="Aún no hay muestra suficiente; se usa rho=-0.05 y NB ratio=1.0."
        >
          DEFAULT CALIBRATION
        </Badge>
      );
    }
    return <Badge tone="slate" testId="totals-model-mode-unavailable">UNAVAILABLE</Badge>;
  })();

  // NB active / DC active badges
  const nbActive = Number.isFinite(Number(ratio)) && Number(ratio) > 1.0001;
  const dcActive = Number.isFinite(Number(rho)) && Number(rho) < 0;

  // Action / recommendation narrative
  let actionTone = 'slate';
  let actionBadge = null;
  let actionText = '';
  const d35 = Number(u35.delta_pts);
  if (isUnder35) {
    actionBadge = (
      <Badge tone="emerald" testId="totals-model-action-badge">UNDER 3.5 SUPPORTED</Badge>
    );
    actionTone = 'emerald';
    actionText = 'El modelo DC/NB refuerza el Under 3.5 frente al Poisson base.';
  } else if (isUnder25) {
    actionBadge = (
      <Badge tone="amber" testId="totals-model-action-badge">UNDER 2.5 — MÁS FRÁGIL</Badge>
    );
    actionTone = 'amber';
    actionText = 'El Under 2.5 tiene soporte, pero es más sensible a un escenario 2-1.';
  } else if (!isUnder) {
    actionText = 'El modelo de totales se muestra como contexto; el mercado recomendado no depende directamente del Under.';
  }

  // Delta narrative
  let deltaNarrative = null;
  if (Number.isFinite(d35)) {
    deltaNarrative = d35 >= 0
      ? `DC/NB aumenta la probabilidad de Under en +${d35.toFixed(1)} pts.`
      : `DC/NB reduce la probabilidad de Under en ${Math.abs(d35).toFixed(1)} pts.`;
  }

  return (
    <section
      data-testid={testId || 'football-totals-model-panel'}
      className="border border-border/60 rounded-xl bg-card/50 p-3 flex flex-col gap-2.5"
    >
      <header className="flex items-start justify-between gap-2">
        <div className="flex items-start gap-2 min-w-0">
          <Sigma className="h-4 w-4 text-cyan-300 mt-0.5" />
          <div className="min-w-0">
            <div className="text-xs font-semibold uppercase tracking-wide text-foreground/90">
              Modelo de Totales — DC/NB
            </div>
            <div className="text-[10px] text-muted-foreground">
              Dixon-Coles + NB Condicional
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1 justify-end">
          {modeBadge}
          {dcActive && <Badge tone="cyan" testId="totals-model-dc-active">DC ACTIVE</Badge>}
          {nbActive
            ? <Badge tone="amber" testId="totals-model-nb-active">NB ACTIVE</Badge>
            : <Badge tone="slate" testId="totals-model-nb-inert">NB INERT</Badge>}
        </div>
      </header>

      {/* Under 2.5 row */}
      <div className="border border-border/40 rounded-md p-2 flex flex-col gap-1">
        <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Under 2.5</div>
        <div className="grid grid-cols-3 gap-2">
          <StatRow label="Poisson"  value={pct(u25.poisson)}   testId="totals-model-u25-poisson" />
          <StatRow label="DC/NB"    value={pct(u25.dc_nb)}     testId="totals-model-u25-dcnb" />
          <StatRow
            label="Delta"
            value={deltaPts(u25.delta_pts)}
            testId="totals-model-u25-delta"
            tone={Number(u25.delta_pts) >= 0 ? 'emerald' : 'rose'}
          />
        </div>
      </div>

      {/* Under 3.5 row */}
      <div className="border border-border/40 rounded-md p-2 flex flex-col gap-1">
        <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Under 3.5</div>
        <div className="grid grid-cols-3 gap-2">
          <StatRow label="Poisson"  value={pct(u35.poisson)}   testId="totals-model-u35-poisson" />
          <StatRow label="DC/NB"    value={pct(u35.dc_nb)}     testId="totals-model-u35-dcnb" />
          <StatRow
            label="Delta"
            value={deltaPts(u35.delta_pts)}
            testId="totals-model-u35-delta"
            tone={Number(u35.delta_pts) >= 0 ? 'emerald' : 'rose'}
          />
        </div>
      </div>

      {/* Parameters */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
        <span>ρ usado: <span className="text-foreground/90 font-mono-tabular" data-testid="totals-model-rho">{signedFloat(rho, 3)}</span></span>
        <span>NB ratio: <span className="text-foreground/90 font-mono-tabular" data-testid="totals-model-ratio">{Number.isFinite(Number(ratio)) ? Number(ratio).toFixed(2) : 'N/D'}</span></span>
        {totalsModel.offense_bucket && (
          <span>Bucket ofensivo: <span className="text-foreground/90" data-testid="totals-model-offense">{totalsModel.offense_bucket}</span></span>
        )}
        {totalsModel.league_tier && (
          <span>Liga: <span className="text-foreground/90">{totalsModel.league_tier}</span></span>
        )}
        {Number.isFinite(Number(totalsModel.sample_size)) && (
          <span>n: <span className="text-foreground/90 font-mono-tabular">{totalsModel.sample_size}</span></span>
        )}
      </div>

      {/* Action */}
      {(actionBadge || actionText || deltaNarrative) && (
        <div className="border-t border-border/40 pt-2 flex flex-col gap-1">
          {actionBadge && (
            <div className="flex items-center gap-2">
              {actionBadge}
            </div>
          )}
          {actionText && (
            <p className={`text-[11px] leading-snug ${actionTone === 'amber' ? 'text-amber-200' : actionTone === 'emerald' ? 'text-emerald-200' : 'text-muted-foreground'}`}>
              {actionText}
            </p>
          )}
          {deltaNarrative && (
            <p className="text-[11px] text-muted-foreground leading-snug">
              {deltaNarrative}
            </p>
          )}
          {nbActive && (
            <p className="text-[11px] text-amber-200 leading-snug inline-flex items-start gap-1">
              <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
              NB activo: el modelo detecta mayor dispersión de goles.
            </p>
          )}
        </div>
      )}

      {totalsModel.summary && (
        <p className="text-[10px] text-muted-foreground/80 leading-snug inline-flex items-start gap-1">
          <Info className="h-3 w-3 mt-0.5 shrink-0" />
          {totalsModel.summary}
        </p>
      )}
    </section>
  );
}

// ─────────────────────────────────────────────────────────────────────
// 2) FootballOverSupportPanel
// ─────────────────────────────────────────────────────────────────────
const OVER_MARKET_LABEL = {
  OVER_1_5:        'Más de 1.5 goles',
  OVER_2_5:        'Más de 2.5 goles',
  '1H_OVER_0_5':   'Más de 0.5 — 1er tiempo',
  TEAM_TOTAL_OVER: 'Team Total Over',
  NONE:            'Sin soporte de Over',
};

export function FootballOverSupportPanel({ overSupport, testId }) {
  if (!overSupport || !overSupport.available) return null;

  const fragilityTone =
    overSupport.fragility_score >= 70 ? 'rose'
    : overSupport.fragility_score >= 50 ? 'amber'
    : 'emerald';

  const recMarket = overSupport.recommended_over_market || 'NONE';
  const recLabel = OVER_MARKET_LABEL[recMarket] || recMarket;

  return (
    <section
      data-testid={testId || 'football-over-support-panel'}
      className="border border-border/60 rounded-xl bg-card/50 p-3 flex flex-col gap-2.5"
    >
      <header className="flex items-start justify-between gap-2">
        <div className="flex items-start gap-2 min-w-0">
          <Sparkles className="h-4 w-4 text-amber-300 mt-0.5" />
          <div className="min-w-0">
            <div className="text-xs font-semibold uppercase tracking-wide text-foreground/90">
              Modelo Over — Soporte
            </div>
            <div className="text-[10px] text-muted-foreground">
              Gol temprano 0–30 + presión ofensiva
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1 justify-end">
          {overSupport.offense_bucket === 'HIGH_OFFENSE' && (
            <Badge tone="emerald" testId="over-support-high-offense">HIGH OFFENSE</Badge>
          )}
          {overSupport.offense_bucket === 'LOW_OFFENSE' && (
            <Badge tone="slate" testId="over-support-low-offense">LOW OFFENSE</Badge>
          )}
          {recMarket === 'OVER_1_5' && (
            <Badge tone="cyan" testId="over-support-over-1-5">OVER 1.5 SUPPORTED</Badge>
          )}
          {recMarket === 'OVER_2_5' && (
            <Badge tone="emerald" testId="over-support-over-2-5">OVER 2.5 SUPPORTED</Badge>
          )}
          {(recMarket === 'NONE' || overSupport.mode === 'observe_only') && (
            <Badge tone="slate" testId="over-support-observe-only">OBSERVE ONLY</Badge>
          )}
        </div>
      </header>

      {/* Score cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <div className="border border-border/40 rounded-md p-2 flex flex-col gap-0.5" data-testid="over-support-score-1-5">
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">Over 1.5</span>
          <span className="text-base font-mono-tabular text-foreground">{overSupport.over_1_5_support_score}/100</span>
        </div>
        <div className="border border-border/40 rounded-md p-2 flex flex-col gap-0.5" data-testid="over-support-score-2-5">
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">Over 2.5</span>
          <span className="text-base font-mono-tabular text-foreground">{overSupport.over_2_5_support_score}/100</span>
        </div>
        <div className="border border-border/40 rounded-md p-2 flex flex-col gap-0.5" data-testid="over-support-score-1h">
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">1T 0.5</span>
          <span className="text-base font-mono-tabular text-foreground">{overSupport.first_half_goal_support_score}/100</span>
        </div>
        <div className="border border-border/40 rounded-md p-2 flex flex-col gap-0.5" data-testid="over-support-score-egp">
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">Gol 0–30</span>
          <span className="text-base font-mono-tabular text-foreground">{overSupport.early_goal_pressure_score}/100</span>
        </div>
      </div>

      {/* Early goal 0–30 per team */}
      <div className="border-t border-border/40 pt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
        <div className="flex items-center justify-between gap-2">
          <span className="text-muted-foreground inline-flex items-center gap-1">
            <Clock4 className="h-3 w-3" /> Local 0–30
          </span>
          <span className="font-mono-tabular text-foreground" data-testid="over-support-home-egp">
            {pct(overSupport.home_early_goal_30_pct)}
          </span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-muted-foreground inline-flex items-center gap-1">
            <Clock4 className="h-3 w-3" /> Visitante 0–30
          </span>
          <span className="font-mono-tabular text-foreground" data-testid="over-support-away-egp">
            {pct(overSupport.away_early_goal_30_pct)}
          </span>
        </div>
        <div className="flex items-center justify-between gap-2 col-span-2">
          <span className="text-muted-foreground inline-flex items-center gap-1">
            <TrendingUp className="h-3 w-3" /> Presencia 0–30 combinada
          </span>
          <span className="font-mono-tabular text-foreground" data-testid="over-support-combined">
            {pct(overSupport.combined_first_30_goal_presence)}
          </span>
        </div>
      </div>

      {/* Recommended market + fragility */}
      <div className="border-t border-border/40 pt-2 flex flex-col gap-1">
        <div className="flex items-center gap-2 flex-wrap text-xs">
          <span className="text-muted-foreground">Mercado sugerido:</span>
          <span className="font-semibold text-foreground" data-testid="over-support-recommended">
            {recLabel}
          </span>
        </div>
        <div className="flex items-center gap-3 text-[10px]">
          <span className="text-muted-foreground">Fragilidad Over 2.5:</span>
          <span
            className={`font-mono-tabular ${fragilityTone === 'rose' ? 'text-rose-200' : fragilityTone === 'amber' ? 'text-amber-200' : 'text-emerald-200'}`}
            data-testid="over-support-fragility"
          >
            {overSupport.fragility_score}/100
          </span>
          {overSupport.defensive_leak_score !== undefined && (
            <span className="text-muted-foreground inline-flex items-center gap-1">
              <Shield className="h-3 w-3" />
              Leak defensivo: <span className="font-mono-tabular text-foreground/90">{overSupport.defensive_leak_score}/100</span>
            </span>
          )}
        </div>
        {Array.isArray(overSupport.reason_codes) && overSupport.reason_codes.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1" data-testid="over-support-codes">
            {overSupport.reason_codes.slice(0, 8).map((rc) => {
              const tone =
                rc === 'OVER_2_5_FRAGILE' || rc === 'CONTROLLED_MATCH_BLOCKS_OVER' || rc === 'TOP_SCORER_OUT_WEAKENS_OVER'
                  ? 'rose'
                  : rc === 'OVER_1_5_PROTECTED' || rc === 'EARLY_GOAL_30_SUPPORT' || rc === 'BOTH_TEAMS_SCORE_EARLY'
                  ? 'emerald'
                  : 'slate';
              return <Badge key={rc} tone={tone}>{rc}</Badge>;
            })}
            {overSupport.reason_codes.length > 8 && (
              <span className="text-[9px] text-muted-foreground">+{overSupport.reason_codes.length - 8}</span>
            )}
          </div>
        )}
      </div>

      {overSupport.summary && (
        <p className="text-[10px] text-muted-foreground/80 leading-snug inline-flex items-start gap-1">
          <Info className="h-3 w-3 mt-0.5 shrink-0" />
          {overSupport.summary}
        </p>
      )}
    </section>
  );
}
