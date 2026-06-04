/**
 * Football Moneyball Panels — minimal viable UI for the new Football
 * Moneyball Intelligence Layer + Pattern Memory backend.
 *
 * Renders three independent, fail-soft panels:
 *   1. FootballIntelligencePanel   — goal_pressure_profile + market_selection
 *   2. FootballPatternMemoryPanel  — historical_pattern_match summary
 *   3. FootballLiveVsPregamePanel  — diff produced by live re-evaluation
 *
 * All three panels return null when their corresponding payload is
 * missing or marked unavailable, so adding them to a MatchCard does not
 * affect non-football picks or fail-soft fallbacks.
 */

import { ShieldCheck, Brain, Activity, AlertTriangle, ArrowRight } from 'lucide-react';

// ─────────────────────────────────────────────────────────────────────
// Shared helpers
// ─────────────────────────────────────────────────────────────────────
const TIER_TONE = {
  HIGH_PRESSURE:     { label: 'Presión alta',     tone: 'rose'    },
  MODERATE_PRESSURE: { label: 'Presión moderada', tone: 'amber'   },
  NEUTRAL_PRESSURE:  { label: 'Presión neutra',   tone: 'slate'   },
  LOW_PRESSURE:      { label: 'Presión baja',     tone: 'emerald' },
  UNAVAILABLE:       { label: 'Sin datos',        tone: 'slate'   },
};

const TONE_CLS = {
  rose:    'bg-rose-500/15 text-rose-200 border-rose-500/30',
  amber:   'bg-amber-500/15 text-amber-200 border-amber-500/30',
  slate:   'bg-slate-500/15 text-slate-200 border-slate-500/30',
  emerald: 'bg-emerald-500/15 text-emerald-200 border-emerald-500/30',
  cyan:    'bg-cyan-500/15 text-cyan-200 border-cyan-500/30',
  violet:  'bg-violet-500/15 text-violet-200 border-violet-500/30',
};

function TierChip({ tier, testId }) {
  const meta = TIER_TONE[tier] || TIER_TONE.UNAVAILABLE;
  return (
    <span
      data-testid={testId}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[10px] font-semibold uppercase tracking-wide ${TONE_CLS[meta.tone]}`}
    >
      {meta.label}
    </span>
  );
}

function StatBlock({ label, value, testId }) {
  if (value === null || value === undefined || value === '') return null;
  return (
    <div className="flex flex-col gap-0.5 min-w-0" data-testid={testId}>
      <span className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</span>
      <span className="text-xs font-mono-tabular text-foreground truncate">{value}</span>
    </div>
  );
}

function ReasonCodes({ codes, testId }) {
  if (!Array.isArray(codes) || codes.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1" data-testid={testId}>
      {codes.slice(0, 8).map((rc) => (
        <span
          key={rc}
          className="inline-flex items-center px-1.5 py-0.5 rounded-md text-[9px] font-mono uppercase border border-border/60 bg-muted/30 text-muted-foreground"
        >
          {rc}
        </span>
      ))}
      {codes.length > 8 && (
        <span className="text-[9px] text-muted-foreground">+{codes.length - 8}</span>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// 1) FootballIntelligencePanel
// ─────────────────────────────────────────────────────────────────────
export function FootballIntelligencePanel({ pick, testId }) {
  if (!pick) return null;

  const pressure   = pick.goal_pressure_profile;
  const marketSel  = pick.market_selection;
  const patternKeys = Array.isArray(pick.football_pattern_keys) ? pick.football_pattern_keys : [];

  // Render only when at least one Moneyball signal exists.
  const hasPressure  = pressure && pressure.available;
  const hasSelection = marketSel && (marketSel.recommended_market || marketSel.protected_alternative);
  if (!hasPressure && !hasSelection && patternKeys.length === 0) return null;

  const combined = pressure?.combined || {};
  const home = pressure?.home || {};
  const away = pressure?.away || {};

  return (
    <section
      data-testid={testId || `football-intel-panel-${pick.match_id || 'na'}`}
      className="border border-border/60 rounded-xl bg-card/50 p-3 flex flex-col gap-2.5"
    >
      <header className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <ShieldCheck className="h-4 w-4 text-cyan-300" />
          <span className="text-xs font-semibold uppercase tracking-wide text-foreground/90">
            Football Moneyball
          </span>
        </div>
        {hasPressure && (
          <TierChip
            tier={combined.pressure_tier}
            testId={`football-intel-tier-${pick.match_id || 'na'}`}
          />
        )}
      </header>

      {hasPressure && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <StatBlock
            label="Home tier"
            value={TIER_TONE[home.pressure_tier]?.label || home.pressure_tier || '—'}
            testId="football-intel-home-tier"
          />
          <StatBlock
            label="Away tier"
            value={TIER_TONE[away.pressure_tier]?.label || away.pressure_tier || '—'}
            testId="football-intel-away-tier"
          />
          <StatBlock
            label="Under 2.5 avg"
            value={
              combined.inputs?.under_2_5_rate_avg !== null && combined.inputs?.under_2_5_rate_avg !== undefined
                ? `${Math.round(combined.inputs.under_2_5_rate_avg * 100)}%`
                : null
            }
          />
          <StatBlock
            label="Under 3.5 avg"
            value={
              combined.inputs?.under_3_5_rate_avg !== null && combined.inputs?.under_3_5_rate_avg !== undefined
                ? `${Math.round(combined.inputs.under_3_5_rate_avg * 100)}%`
                : null
            }
          />
        </div>
      )}

      {hasSelection && (
        <div className="border-t border-border/40 pt-2 flex flex-col gap-1.5">
          <div className="flex items-center gap-2 flex-wrap text-xs">
            <span className="text-muted-foreground">Mercado base:</span>
            <span className="font-semibold text-foreground" data-testid="football-intel-recommended-market">
              {marketSel.recommended_market}
            </span>
            {marketSel.protected_alternative && (
              <>
                <ArrowRight className="h-3 w-3 text-cyan-300 shrink-0" />
                <span
                  className={`inline-flex items-center px-1.5 py-0.5 rounded-md border text-[10px] font-semibold ${TONE_CLS.cyan}`}
                  data-testid="football-intel-protected-alt"
                >
                  Protección: {marketSel.protected_alternative}
                </span>
              </>
            )}
          </div>
          <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
            {Number.isFinite(marketSel.market_confidence) && (
              <span>Confianza: <span className="font-mono-tabular text-foreground/90">{marketSel.market_confidence}/100</span></span>
            )}
            {Number.isFinite(marketSel.fragility) && (
              <span>Fragilidad: <span className="font-mono-tabular text-foreground/90">{marketSel.fragility}/100</span></span>
            )}
            {marketSel.requires_manual_odds && (
              <span className="text-amber-200 inline-flex items-center gap-1">
                <AlertTriangle className="h-3 w-3" /> Cuotas manuales
              </span>
            )}
            {marketSel.watchlist && (
              <span className={`inline-flex items-center px-1.5 py-0.5 rounded-md border text-[9px] ${TONE_CLS.amber}`}>
                Watchlist
              </span>
            )}
          </div>
          {marketSel.why_this_market && (
            <p className="text-[11px] text-muted-foreground leading-snug">
              {marketSel.why_this_market}
            </p>
          )}
          <ReasonCodes codes={marketSel.reason_codes} testId="football-intel-reason-codes" />
        </div>
      )}

      {patternKeys.length > 0 && (
        <div className="border-t border-border/40 pt-2">
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
            Patrones detectados
          </span>
          <div className="flex flex-wrap gap-1 mt-1" data-testid="football-intel-pattern-keys">
            {patternKeys.map((k) => (
              <span
                key={k}
                className={`inline-flex items-center px-1.5 py-0.5 rounded-md border text-[9px] font-mono uppercase ${TONE_CLS.violet}`}
              >
                {k}
              </span>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

// ─────────────────────────────────────────────────────────────────────
// 2) FootballPatternMemoryPanel
// ─────────────────────────────────────────────────────────────────────
export function FootballPatternMemoryPanel({ pick, testId }) {
  if (!pick) return null;
  const hp = pick.historical_pattern_match;
  if (!hp || (!hp.matched_patterns && hp.sample_size === 0)) return null;

  const sample = hp.sample_size || 0;
  const hitRate = hp.historical_hit_rate;
  const roi = hp.historical_roi;
  const bestMarket = hp.best_historical_market;
  const adj = hp.pattern_confidence_adjustment;
  const enabled = hp.enabled !== false;

  return (
    <section
      data-testid={testId || `football-pattern-memory-${pick.match_id || 'na'}`}
      className="border border-border/60 rounded-xl bg-card/50 p-3 flex flex-col gap-2"
    >
      <header className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Brain className="h-4 w-4 text-violet-300" />
          <span className="text-xs font-semibold uppercase tracking-wide text-foreground/90">
            Pattern Memory (Football)
          </span>
        </div>
        {!enabled && (
          <span className={`inline-flex items-center px-1.5 py-0.5 rounded-md border text-[9px] ${TONE_CLS.slate}`}>
            Disabled
          </span>
        )}
      </header>

      {hp.warning && (
        <p className="text-[11px] text-amber-200 leading-snug inline-flex items-start gap-1">
          <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
          {hp.warning}
        </p>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <StatBlock label="n" value={sample} testId="football-pattern-sample" />
        <StatBlock
          label="Hit rate"
          value={hitRate !== null && hitRate !== undefined ? `${Math.round(hitRate * 100)}%` : '—'}
          testId="football-pattern-hit-rate"
        />
        <StatBlock
          label="ROI"
          value={roi !== null && roi !== undefined ? `${(roi * 100).toFixed(1)}%` : '—'}
          testId="football-pattern-roi"
        />
        <StatBlock
          label="Ajuste conf."
          value={
            adj !== null && adj !== undefined
              ? (adj > 0 ? `+${adj.toFixed(1)}` : adj.toFixed(1))
              : '—'
          }
          testId="football-pattern-adjustment"
        />
      </div>

      {bestMarket && (
        <div className="text-[11px] text-muted-foreground flex items-center gap-1.5">
          <span>Mejor mercado histórico:</span>
          <span className={`inline-flex items-center px-1.5 py-0.5 rounded-md border text-[10px] font-semibold ${TONE_CLS.cyan}`}>
            {bestMarket}
          </span>
        </div>
      )}

      {Array.isArray(hp.matched_patterns) && hp.matched_patterns.length > 0 && (
        <div className="flex flex-wrap gap-1" data-testid="football-pattern-matched">
          {hp.matched_patterns.map((p) => (
            <span
              key={p}
              className={`inline-flex items-center px-1.5 py-0.5 rounded-md border text-[9px] font-mono uppercase ${
                p === hp.primary_pattern ? TONE_CLS.violet : TONE_CLS.slate
              }`}
            >
              {p}
            </span>
          ))}
        </div>
      )}

      <ReasonCodes codes={hp.pattern_reason_codes} testId="football-pattern-reasons" />
    </section>
  );
}

// ─────────────────────────────────────────────────────────────────────
// 3) FootballLiveVsPregamePanel
// ─────────────────────────────────────────────────────────────────────
const RECOMMENDATION_LABEL = {
  KEEP:   { label: 'Mantener mercado',  tone: 'emerald' },
  REDUCE: { label: 'Reducir exposición', tone: 'amber'   },
  AVOID:  { label: 'Evitar mercado',     tone: 'rose'    },
};

export function FootballLiveVsPregamePanel({ diff, testId }) {
  if (!diff || !diff.available) return null;
  const rec = RECOMMENDATION_LABEL[diff.market_recommendation] || RECOMMENDATION_LABEL.KEEP;

  return (
    <section
      data-testid={testId || 'football-live-vs-pregame-panel'}
      className="border border-border/60 rounded-xl bg-card/50 p-3 flex flex-col gap-2"
    >
      <header className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Activity className="h-4 w-4 text-cyan-300" />
          <span className="text-xs font-semibold uppercase tracking-wide text-foreground/90">
            Live vs Pregame
          </span>
        </div>
        <span
          className={`inline-flex items-center px-2 py-0.5 rounded-md border text-[10px] font-semibold uppercase tracking-wide ${TONE_CLS[rec.tone]}`}
          data-testid="football-live-vs-pregame-recommendation"
        >
          {rec.label}
        </span>
      </header>

      <div className="flex items-center gap-2 flex-wrap text-xs">
        <span className="text-muted-foreground">Pregame:</span>
        <TierChip tier={diff.pregame_tier} testId="football-live-vs-pregame-pre-tier" />
        <ArrowRight className="h-3 w-3 text-muted-foreground" />
        <span className="text-muted-foreground">Live:</span>
        <TierChip tier={diff.live_tier} testId="football-live-vs-pregame-live-tier" />
        {Number.isFinite(diff.tier_shift) && diff.tier_shift !== 0 && (
          <span className="text-[10px] text-muted-foreground">
            shift: <span className="font-mono-tabular text-foreground/90">{diff.tier_shift > 0 ? `+${diff.tier_shift}` : diff.tier_shift}</span>
          </span>
        )}
      </div>

      {diff.summary_es && (
        <p className="text-[11px] text-muted-foreground leading-snug">
          {diff.summary_es}
        </p>
      )}

      <ReasonCodes codes={diff.reason_codes} testId="football-live-vs-pregame-codes" />
    </section>
  );
}
