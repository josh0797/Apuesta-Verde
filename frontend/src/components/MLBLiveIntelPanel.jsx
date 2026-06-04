import { useEffect, useState, useCallback } from 'react';
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  TrendingUp,
  TrendingDown,
  ShieldCheck,
  ShieldAlert,
  Zap,
  RefreshCw,
  Clock,
  CircleAlert,
} from 'lucide-react';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { ExplosiveInningV2Panel } from '@/components/ExplosiveInningV2Panel';

/**
 * MLBLiveIntelPanel — MLB Engine V4 Live Intelligence.
 *
 * Renders ONLY when:
 *   1. sport === 'baseball'
 *   2. match.is_live === true
 *   3. llmPick (pregame pick) exists for this match
 *
 * Calls POST /api/mlb/live/reevaluate with the full pregame_pick + a
 * normalised live_state derived from match data.
 *
 * Panel sections:
 *   - Header: SCRIPT STATUS (🟢 Intact / 🔴 Broken)
 *   - Live Script: new live classification + narrative
 *   - Remaining Runs Projection: current total / projected final / line
 *   - Under Risk monitor (when applicable)
 *   - Cashout Intelligence: HOLD / PARTIAL CASHOUT / FULL CASHOUT
 *   - Volatility chips: per-starter LOW/MEDIUM/HIGH
 *
 * Re-fetches every 60s while the match remains live.
 */

const CASHOUT_STYLES = {
  HOLD: {
    icon: ShieldCheck,
    color: 'emerald',
    bg: 'bg-emerald-500/10 border-emerald-500/35 text-emerald-200',
    label: 'Mantener',
    emoji: '🟢',
  },
  PARTIAL_CASHOUT: {
    icon: ShieldAlert,
    color: 'amber',
    bg: 'bg-amber-500/10 border-amber-500/35 text-amber-200',
    label: 'Cashout parcial',
    emoji: '🟡',
  },
  FULL_CASHOUT: {
    icon: Zap,
    color: 'rose',
    bg: 'bg-rose-500/10 border-rose-500/35 text-rose-200',
    label: 'Cashout total',
    emoji: '🔴',
  },
};

const RISK_STYLES = {
  ON_TRACK:       { color: 'emerald', label: 'En camino',     icon: CheckCircle2 },
  WATCH:          { color: 'amber',   label: 'En observación', icon: Activity },
  UNDER_IN_DANGER:{ color: 'rose',    label: 'En peligro',     icon: AlertTriangle },
  UNDER_BUSTED:   { color: 'rose',    label: 'Bustado',        icon: CircleAlert },
  NOT_APPLICABLE: { color: 'slate',   label: 'No aplica',      icon: CheckCircle2 },
  UNKNOWN_LINE:   { color: 'slate',   label: 'Línea desconocida', icon: CircleAlert },
};

const VOL_STYLES = {
  LOW:    'bg-emerald-500/10 border-emerald-500/35 text-emerald-200',
  MEDIUM: 'bg-amber-500/10 border-amber-500/35 text-amber-200',
  HIGH:   'bg-rose-500/10 border-rose-500/35 text-rose-200',
};

function deriveLiveState(match, liveDetail) {
  // P4 polish: prefer the canonical snapshot from
  // /api/matches/{id}/live-refresh (liveDetail) — it's always fresh
  // and uses the normalised shape from mlb_live_state.fetch_live_state.
  // Fall back to the `match` doc only when liveDetail is unavailable
  // (e.g. during the first render before the hook resolves).
  if (liveDetail && (liveDetail.inning || liveDetail.score)) {
    const inning   = liveDetail.inning?.number ?? null;
    const half     = (liveDetail.inning?.half || 'top').toLowerCase();
    const homeRuns = liveDetail.score?.home;
    const awayRuns = liveDetail.score?.away;
    if (inning != null) {
      return {
        current_inning: Number(inning),
        is_top_half:    half === 'top',
        home_runs:      Number(homeRuns) || 0,
        away_runs:      Number(awayRuns) || 0,
        home_starter_runs_allowed: null,
        away_starter_runs_allowed: null,
        home_starter_pulled:       null,
        away_starter_pulled:       null,
        bullpen_runs_allowed_home: null,
        bullpen_runs_allowed_away: null,
      };
    }
  }

  if (!match) return null;
  // Best-effort normalisation — supports several shapes the matches API may produce.
  const live = match.live || match.linescore || {};
  const teams = live.teams || {};
  const homeRuns = teams?.home?.runs ?? live.home_runs ?? match.home_runs;
  const awayRuns = teams?.away?.runs ?? live.away_runs ?? match.away_runs;
  const inning   = live.currentInning ?? live.current_inning ?? match.current_inning;
  const inningHalf = (live.inningHalf || live.inning_half || 'top').toLowerCase();
  if (!inning) return null;
  return {
    current_inning: Number(inning),
    is_top_half:    inningHalf === 'top',
    home_runs:      Number(homeRuns) || 0,
    away_runs:      Number(awayRuns) || 0,
    home_starter_runs_allowed: live?.teams?.home?.starter_runs_allowed ?? null,
    away_starter_runs_allowed: live?.teams?.away?.starter_runs_allowed ?? null,
    home_starter_pulled:       live?.teams?.home?.starter_pulled ?? null,
    away_starter_pulled:       live?.teams?.away?.starter_pulled ?? null,
    bullpen_runs_allowed_home: live?.teams?.home?.bullpen_runs ?? null,
    bullpen_runs_allowed_away: live?.teams?.away?.bullpen_runs ?? null,
  };
}

function ScriptStatusBadge({ broken, severity }) {
  if (broken) {
    const sevText = severity === 'STRONG' ? 'Roto (severo)' : severity === 'MILD' ? 'En riesgo' : 'Roto';
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs border bg-rose-500/15 border-rose-500/40 text-rose-200" data-testid="mlb-live-script-status">
        <AlertTriangle className="h-3.5 w-3.5" />
        🔴 Script {sevText}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs border bg-emerald-500/15 border-emerald-500/40 text-emerald-200" data-testid="mlb-live-script-status">
      <CheckCircle2 className="h-3.5 w-3.5" />
      🟢 Script intacto
    </span>
  );
}

function MetricTile({ label, value, sub, tone = 'neutral', testId }) {
  const toneCls =
    tone === 'positive' ? 'text-emerald-200' :
    tone === 'negative' ? 'text-rose-200' :
    tone === 'warning'  ? 'text-amber-200' :
                          'text-foreground/90';
  return (
    <div className="rounded-lg border border-border/50 bg-white/[0.02] px-3 py-2" data-testid={testId}>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className={`text-base font-semibold tabular-nums ${toneCls} leading-tight`}>{value}</div>
      {sub ? <div className="text-[10px] text-muted-foreground mt-0.5">{sub}</div> : null}
    </div>
  );
}

function VolatilityChip({ side, vol, name }) {
  if (!vol) return null;
  const cls = VOL_STYLES[vol.level] || VOL_STYLES.LOW;
  return (
    <div className={`flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-md border ${cls}`} data-testid={`mlb-live-vol-${side}`}>
      <div className="flex items-center gap-1.5 min-w-0">
        <span className="text-[10px] uppercase tracking-wide opacity-70">
          {side === 'home' ? 'Local' : 'Visitante'}
        </span>
        {name ? <span className="text-xs font-medium truncate" title={name}>{name}</span> : null}
      </div>
      <div className="flex items-center gap-1.5 shrink-0">
        <span className="text-[10px] opacity-80">Vol</span>
        <span className="text-xs font-bold">{vol.level}</span>
        {typeof vol.penalty === 'number' && vol.penalty > 0 ? (
          <span className="text-[10px] tabular-nums opacity-90">−{vol.penalty}</span>
        ) : null}
      </div>
    </div>
  );
}

export function MLBLiveIntelPanel({ sport, match, llmPick, liveDetail, effectiveIsLive }) {
  // Triple-gate — render nothing if conditions aren't met.
  // P4 polish: prefer `effectiveIsLive` from the live-refresh hook
  // (it doesn't rely on stale `match.is_live`). Also accept any of:
  //   * liveDetail.is_live === true
  //   * liveDetail.state === 'live-data-ready' | 'live-data-partial'
  //   * liveDetail.inning?.number >= 1
  //   * sum(liveDetail.score.{home,away}) > 0
  // so the panel activates even when the matches doc hasn't refreshed.
  const liveScoreSum = (liveDetail?.score?.home ?? 0) + (liveDetail?.score?.away ?? 0);
  const liveInning   = liveDetail?.inning?.number ?? null;
  const isLiveDerived =
    effectiveIsLive === true ||
    liveDetail?.is_live === true ||
    liveDetail?.state === 'live-data-ready' ||
    liveDetail?.state === 'live-data-partial' ||
    (liveInning != null && liveInning >= 1) ||
    liveScoreSum > 0 ||
    Boolean(match?.is_live);

  const gateOk =
    String(sport || '').toLowerCase() === 'baseball' &&
    isLiveDerived &&
    Boolean(llmPick && llmPick._mlb_script_v3);

  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState(null);
  const [lastFetch, setLastFetch] = useState(null);

  const fetchIntel = useCallback(async () => {
    if (!gateOk) return;
    setLoading(true);
    setError(null);
    try {
      const liveState = deriveLiveState(match, liveDetail);
      const body = {
        match_id:     String(match?.match_id ?? match?.id ?? liveDetail?.game_pk ?? ''),
        pregame_pick: llmPick,
        live_state:   liveState || null,
      };
      const res = await api.post('/mlb/live/reevaluate', body);
      setData(res.data || null);
      setLastFetch(new Date());
    } catch (e) {
      const detail = e?.response?.data?.detail || e?.message || 'Error obteniendo live intelligence';
      setError(detail);
    } finally {
      setLoading(false);
    }
  }, [gateOk, match, llmPick, liveDetail]);

  useEffect(() => {
    if (!gateOk) return;
    fetchIntel();
    const id = setInterval(fetchIntel, 60_000);  // refresh every 60s while live
    return () => clearInterval(id);
  }, [fetchIntel, gateOk]);

  if (!gateOk) return null;

  // Status handling — backend may return NOT_LIVE_YET when match is between
  // pregame and first pitch. P4 polish: distinguish the case where we
  // already have score/inning > 0 (so it's actually a "live feed
  // incomplete" situation, NOT a pre-game wait).
  if (data?.status === 'NOT_LIVE_YET') {
    const liveButFeedIncomplete = liveScoreSum > 0 || (liveInning != null && liveInning >= 1);
    return (
      <section className="rounded-xl border border-border/50 bg-card/40 p-4" data-testid="mlb-live-intel-panel-pending">
        <header className="flex items-center justify-between gap-3 mb-2">
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-cyan-300" />
            <h3 className="text-sm font-semibold uppercase tracking-wide text-foreground/95">
              Live Intelligence MLB
            </h3>
          </div>
          <span className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground" data-testid="mlb-live-intel-pending-chip">
            <Clock className="h-3 w-3" />
            {liveButFeedIncomplete ? 'Live feed incompleto' : 'Esperando inicio del partido'}
          </span>
        </header>
        <p className="text-xs text-muted-foreground">
          {liveButFeedIncomplete
            ? `El partido está en curso (inning ${liveInning ?? '—'}, ${liveScoreSum} carrera${liveScoreSum === 1 ? '' : 's'}) pero faltan datos canónicos del live feed. Reintentando…`
            : (data.detail || 'El partido aún no está en vivo; live intelligence se activará al primer inning.')}
        </p>
      </section>
    );
  }

  const intel = data?.intelligence || null;
  const sb   = intel?.script_break  || null;
  const lsc  = intel?.live_script   || null;
  const ur   = intel?.under_risk    || null;
  const co   = intel?.cashout       || null;
  const vol  = intel?.volatility    || null;
  const ev2  = intel?.explosive_v2  || null;

  const cashoutStyle = co?.verdict ? CASHOUT_STYLES[co.verdict] || CASHOUT_STYLES.HOLD : null;
  const CashoutIcon = cashoutStyle?.icon || ShieldCheck;
  const riskStyle = ur?.verdict ? RISK_STYLES[ur.verdict] || RISK_STYLES.NOT_APPLICABLE : null;
  const RiskIcon = riskStyle?.icon || CheckCircle2;

  const pitchersBlock = llmPick?._mlb_script_v3?.pitchers_block || {};

  return (
    <section
      className="rounded-xl border border-border/50 bg-card/40 p-4 space-y-4"
      data-testid="mlb-live-intel-panel"
    >
      {/* Header */}
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-cyan-300" />
          <h3 className="text-sm font-semibold uppercase tracking-wide text-foreground/95">
            Live Intelligence MLB
          </h3>
          {sb ? <ScriptStatusBadge broken={sb.broken} severity={sb.severity} /> : null}
        </div>
        <div className="flex items-center gap-2">
          {lastFetch ? (
            <span className="text-[10px] text-muted-foreground">
              Actualizado {lastFetch.toLocaleTimeString()}
            </span>
          ) : null}
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={fetchIntel}
            disabled={loading}
            data-testid="mlb-live-intel-refresh"
            className="h-7 px-2 text-[11px]"
          >
            <RefreshCw className={`h-3.5 w-3.5 mr-1 ${loading ? 'animate-spin' : ''}`} />
            Recalcular
          </Button>
        </div>
      </header>

      {/* Error or loading state */}
      {error ? (
        <div className="text-xs text-rose-300 border border-rose-500/30 bg-rose-500/10 rounded-md px-3 py-2" data-testid="mlb-live-intel-error">
          {error}
        </div>
      ) : null}
      {loading && !data ? (
        <div className="space-y-2">
          <Skeleton className="h-6 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : null}

      {/* Live Script narrative */}
      {lsc ? (
        <div className="rounded-lg border border-border/40 bg-white/[0.02] px-3 py-2.5 space-y-1.5">
          <div className="flex items-center justify-between gap-2">
            <span className="inline-flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-cyan-300/90">
              <TrendingUp className="h-3 w-3" />
              Script live
            </span>
            <span className="text-xs font-semibold text-foreground/95" data-testid="mlb-live-script-code">
              {lsc.label_es || lsc.live_script}
            </span>
          </div>
          {lsc.narrative_es ? (
            <p className="text-xs text-foreground/80 leading-relaxed border-l-2 border-cyan-500/40 pl-2">
              {lsc.narrative_es}
            </p>
          ) : null}
        </div>
      ) : null}

      {/* Explosive Inning v2 — per-inning state classifier */}
      {ev2 ? <ExplosiveInningV2Panel payload={ev2} idx={0} /> : null}

      {/* Run projection tiles */}
      {lsc ? (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <MetricTile
            label="Total actual"
            value={lsc.current_total ?? 0}
            sub={`Innings ${lsc.innings_played ?? '—'}`}
            testId="mlb-live-current-total"
          />
          <MetricTile
            label="Proy. final"
            value={lsc.projected_final_total != null ? Number(lsc.projected_final_total).toFixed(1) : '—'}
            sub={`Pace ${lsc.run_pace_per_inning ?? '—'}/inn`}
            tone={
              ur?.line && lsc.projected_final_total != null
                ? lsc.projected_final_total > ur.line ? 'negative' : 'positive'
                : 'neutral'
            }
            testId="mlb-live-projected-final"
          />
          <MetricTile
            label="ER pregame"
            value={lsc.expected_total_pregame != null ? Number(lsc.expected_total_pregame).toFixed(1) : '—'}
            sub="Modelo Poisson"
            testId="mlb-live-er-pregame"
          />
          <MetricTile
            label="WP local / visit."
            value={`${Math.round(lsc.win_probability_home ?? 50)}% / ${Math.round(lsc.win_probability_away ?? 50)}%`}
            sub={`Bullpen ${Math.round(lsc.bullpen_pressure ?? 0)}/100`}
            testId="mlb-live-wp"
          />
        </div>
      ) : null}

      {/* Under risk monitor */}
      {ur && ur.is_under_pick && ur.verdict !== 'NOT_APPLICABLE' ? (
        <div className={`rounded-lg border px-3 py-2.5 ${
          riskStyle?.color === 'rose' ? 'border-rose-500/35 bg-rose-500/10' :
          riskStyle?.color === 'amber' ? 'border-amber-500/35 bg-amber-500/10' :
          'border-emerald-500/35 bg-emerald-500/10'
        }`} data-testid="mlb-live-under-risk">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <RiskIcon className="h-4 w-4" />
              <span className="text-xs uppercase tracking-wide opacity-90">Riesgo Under {ur.line ?? ''}</span>
              <span className="text-sm font-semibold">{riskStyle?.label}</span>
            </div>
            <span className="text-sm font-mono tabular-nums">{Math.round(ur.risk_score ?? 0)}%</span>
          </div>
          {ur.narrative_es ? (
            <p className="text-[11px] mt-1.5 opacity-90 leading-snug">{ur.narrative_es}</p>
          ) : null}
        </div>
      ) : null}

      {/* Cashout Intelligence */}
      {co ? (
        <div className={`rounded-lg border px-3 py-3 ${cashoutStyle?.bg || ''}`} data-testid="mlb-live-cashout">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-2 min-w-0">
              <CashoutIcon className="h-5 w-5 shrink-0" />
              <div className="min-w-0">
                <div className="text-[10px] uppercase tracking-wide opacity-80">Cashout Intelligence</div>
                <div className="text-base font-bold" data-testid="mlb-live-cashout-verdict">
                  {cashoutStyle?.emoji} {cashoutStyle?.label}
                </div>
              </div>
            </div>
            <span className="text-[10px] font-mono tabular-nums opacity-80">
              Conf {Math.round(co.confidence ?? 0)}
            </span>
          </div>
          {co.narrative_es ? (
            <p className="text-[11px] mt-2 opacity-95 leading-relaxed">{co.narrative_es}</p>
          ) : null}
          {Array.isArray(co.reasons) && co.reasons.length > 1 ? (
            <ul className="mt-1.5 space-y-0.5">
              {co.reasons.slice(1).map((r, i) => (
                <li key={i} className="flex items-start gap-1.5 text-[11px] opacity-90">
                  <span className="opacity-60">·</span>
                  <span>{r}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {/* Volatility chips */}
      {vol ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <VolatilityChip side="away" vol={vol.away} name={pitchersBlock?.away?.name} />
          <VolatilityChip side="home" vol={vol.home} name={pitchersBlock?.home?.name} />
        </div>
      ) : null}

      {/* Script break reasons (collapsed list when broken) */}
      {sb?.broken && Array.isArray(sb.reasons) && sb.reasons.length > 0 ? (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/5 px-3 py-2" data-testid="mlb-live-script-break-reasons">
          <div className="text-[11px] uppercase tracking-wide text-rose-200/90 mb-1 flex items-center gap-1.5">
            <TrendingDown className="h-3 w-3" />
            Por qué el script se rompió
          </div>
          <ul className="space-y-0.5">
            {sb.reasons.map((r, i) => (
              <li key={i} className="text-[11px] text-rose-200/85 flex items-start gap-1.5">
                <span className="opacity-60">·</span>
                <span>{r}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
