import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Compass,
  Activity,
  Gauge,
  Flag,
  Layers,
  TrendingUp,
  Hourglass,
  Loader2,
  RefreshCw,
  Eye,
  EyeOff,
  AlertTriangle,
  Sparkles,
  Crosshair,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { api } from '@/lib/api';

/**
 * LiveTerritorialControlPanel — frontend for the new Live Territorial
 * Control + Corner Intelligence engines (FOOTBALL ONLY).
 *
 * Renders three blocks:
 *   1. 📐 CONTROL TERRITORIAL — state, posesión, xT, presión, conversión.
 *   2. 🏁 CORNER INTELLIGENCE — score 0-100 + market recommendations.
 *   3. RANKING DINÁMICO DE MERCADOS LIVE — ordered list with score chips.
 *
 * The corner recommendation is HIDDEN until the corner score crosses
 * `surface_threshold` (per the user's rule "Show score internally always,
 * but only surface recommendations once score exceeds the minimum
 * threshold").
 *
 * Auto-fetches the evaluation when the panel mounts and re-evaluates
 * every 60s while the match is live.
 */

const STATE_LABEL_ES = {
  NO_CLEAR_DOMINANCE:    'Sin dominio claro',
  TERRITORIAL_CONTROL:   'Control sin profundidad',
  CONTROL_WITH_PRESSURE: 'Control con presión real',
  CORNER_PRESSURE_STATE: 'Escenario de córners',
};

const STATE_TONE_CLASSES = {
  slate:   'bg-slate-500/10 border-slate-500/35 text-slate-200',
  sky:     'bg-sky-500/10 border-sky-500/35 text-sky-200',
  emerald: 'bg-emerald-500/10 border-emerald-500/35 text-emerald-200',
  violet:  'bg-violet-500/10 border-violet-500/35 text-violet-200',
};

const CATEGORY_ICON = {
  OVER_TEAM_CORNERS:  Flag,
  TEAM_MOST_CORNERS:  Flag,
  NEXT_CORNER:        Flag,
  NEXT_GOAL:          Crosshair,
  OVER_GOALS:         TrendingUp,
  BTTS:               TrendingUp,
  WAIT:               Hourglass,
};

const CATEGORY_TONE = {
  OVER_TEAM_CORNERS:  'violet',
  TEAM_MOST_CORNERS:  'violet',
  NEXT_CORNER:        'violet',
  NEXT_GOAL:          'emerald',
  OVER_GOALS:         'emerald',
  BTTS:               'emerald',
  WAIT:               'slate',
};

const PRESSURE_TONE = {
  BAJA:  'slate',
  MEDIA: 'amber',
  ALTA:  'rose',
};

function StatTile({ label, value, sub, tone = 'default', testId }) {
  const tones = {
    default: 'bg-white/[0.03] border-border/40',
    sky:     'bg-sky-500/8 border-sky-500/30',
    violet:  'bg-violet-500/8 border-violet-500/30',
    emerald: 'bg-emerald-500/8 border-emerald-500/30',
    amber:   'bg-amber-500/8 border-amber-500/30',
    rose:    'bg-rose-500/8 border-rose-500/30',
    slate:   'bg-slate-500/8 border-slate-500/30',
  };
  return (
    <div
      className={`rounded-md border px-2.5 py-1.5 ${tones[tone] || tones.default}`}
      data-testid={testId}
    >
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="text-sm font-semibold text-foreground/95 tabular-nums">
        {value}
      </div>
      {sub ? (
        <div className="text-[10px] text-muted-foreground mt-0.5">{sub}</div>
      ) : null}
    </div>
  );
}

function MarketRow({ market, index, testIdPrefix }) {
  const Icon = CATEGORY_ICON[market.category] || Activity;
  const tone = CATEGORY_TONE[market.category] || 'slate';
  const cls = STATE_TONE_CLASSES[tone] || STATE_TONE_CLASSES.slate;
  const score = Number(market.score ?? 0);

  return (
    <li
      className={`flex items-start gap-2 px-2.5 py-1.5 rounded-md border ${cls}`}
      data-testid={`${testIdPrefix}-market-${index}`}
    >
      <span className="flex items-center justify-center w-5 h-5 rounded-full bg-white/[0.08] text-[10px] font-bold tabular-nums shrink-0">
        {index + 1}
      </span>
      <Icon className="h-3.5 w-3.5 mt-0.5 shrink-0 opacity-90" />
      <div className="flex-1 min-w-0">
        <div className="text-[13px] font-medium text-foreground/95 truncate">
          {market.market}
        </div>
        {market.rationale ? (
          <div className="text-[10.5px] text-muted-foreground leading-snug">
            {market.rationale}
          </div>
        ) : null}
      </div>
      <span className="font-mono tabular-nums text-[11px] shrink-0 opacity-90">
        {score.toFixed(0)}
      </span>
    </li>
  );
}

export function LiveTerritorialControlPanel({ sport, match, t }) {
  // Initialize all hooks FIRST (React rules - hooks must be called unconditionally)
  const matchId = match?.match_id ?? match?.id ?? null;
  const [payload, setPayload] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [showInternalCorner, setShowInternalCorner] = useState(false);
  const pollRef = useRef(null);

  const fetchEvaluation = async () => {
    if (!matchId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.post('/football/live/territorial_control', {
        match_id: String(matchId),
        refresh:  false,
        surface_threshold: 55,
      });
      const j = res.data || {};
      if (j.ok === false) {
        throw new Error(j.detail || j.error || 'evaluation failed');
      }
      setPayload(j);
    } catch (exc) {
      const detail = exc?.response?.data?.detail || exc?.message || 'Error obteniendo control territorial';
      setError(typeof detail === 'string' ? detail : JSON.stringify(detail));
    } finally {
      setLoading(false);
    }
  };

  // Fetch on mount + every 60s.
  useEffect(() => {
    // Only fetch if sport is football and match is live
    if (sport !== 'football' || !match?.is_live || !matchId) return;
    fetchEvaluation();
    pollRef.current = setInterval(fetchEvaluation, 60_000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matchId, sport, match?.is_live]);

  // Aggregate posesión / xT pretty values.
  const possessionDisplay = useMemo(() => {
    const m = payload?.metrics || {};
    if (m.possession_home == null && m.possession_away == null) return '—';
    return `${Math.round(Number(m.possession_home || 0))}% / ${Math.round(Number(m.possession_away || 0))}%`;
  }, [payload]);

  const xtDisplay = useMemo(() => {
    const m = payload?.metrics || {};
    if (m.xt_home == null && m.xt_away == null) return '—';
    return `${Math.round(Number(m.xt_home || 0))} vs ${Math.round(Number(m.xt_away || 0))}`;
  }, [payload]);

  // Sport gate: football only (AFTER all hooks).
  if (sport !== 'football') return null;
  // Only when the match is live.
  if (!match?.is_live) return null;

  const territorial = payload?.territorial || null;
  const corner = payload?.corner || null;
  const ranked = Array.isArray(payload?.ranked_markets) ? payload.ranked_markets : [];

  // Show the corner recommendation ONLY when the score surfaces
  // (server already computed surface_recommendation) OR when the user
  // explicitly toggled the internal preview on.
  const cornerSurfaced = !!corner?.surface_recommendation;
  const cornerScore = Number(corner?.score ?? 0);
  const cornerThreshold = Number(corner?.surface_threshold ?? 55);

  const stateTone = territorial?.state_tone || 'slate';
  const stateClass = STATE_TONE_CLASSES[stateTone] || STATE_TONE_CLASSES.slate;
  const indicators = territorial?.indicators || {};

  return (
    <section
      className="rounded-xl border border-border bg-card p-5 space-y-4"
      data-testid="live-territorial-panel-root"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Compass className="h-4 w-4 text-cyan-300/90" />
          <h3 className="text-sm font-semibold uppercase tracking-wide text-foreground/95">
            Control territorial · Live
          </h3>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={fetchEvaluation}
          disabled={loading}
          className="h-7 px-2 text-[11px] text-muted-foreground hover:text-foreground"
          data-testid="live-territorial-refresh"
        >
          {loading ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <RefreshCw className="h-3 w-3" />
          )}
          <span className="ml-1.5">Actualizar</span>
        </Button>
      </div>

      {error ? (
        <div
          className="flex items-center gap-2 text-[11px] text-rose-300 bg-rose-500/8 border border-rose-500/25 rounded-md px-2.5 py-1.5"
          data-testid="live-territorial-error"
        >
          <AlertTriangle className="h-3 w-3" />
          {String(error)}
        </div>
      ) : null}

      {loading && !payload ? (
        <div className="space-y-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : !payload ? (
        <div className="text-[12px] text-muted-foreground">
          Sin evaluación disponible.
        </div>
      ) : (
        <>
          {/* State header chip */}
          <div
            className={`flex items-start gap-2 px-3 py-2 rounded-lg border ${stateClass}`}
            data-testid="live-territorial-state"
          >
            <Layers className="h-4 w-4 mt-0.5 shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[10px] uppercase tracking-wide opacity-80">
                  Estado
                </span>
                <span
                  className="text-sm font-semibold"
                  data-testid="live-territorial-state-label"
                >
                  {STATE_LABEL_ES[territorial?.state] || territorial?.state}
                </span>
                {territorial?.corner_pressure_state ? (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/[0.10] border border-current/30 font-mono">
                    CORNER_PRESSURE
                  </span>
                ) : null}
              </div>
              <p className="text-[11px] opacity-90 leading-snug mt-0.5">
                {territorial?.reasoning_es}
              </p>
            </div>
          </div>

          {/* Stat tiles (Posesión · xT · Presión · Conversión) */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <StatTile
              label="Posesión"
              value={possessionDisplay}
              tone="sky"
              testId="live-territorial-tile-possession"
            />
            <StatTile
              label="Amenaza (xT)"
              value={xtDisplay}
              tone="violet"
              testId="live-territorial-tile-xt"
            />
            <StatTile
              label="Presión"
              value={indicators.pressure_level || '—'}
              sub={
                indicators.pressure_score != null
                  ? `${indicators.pressure_score.toFixed(0)}/100`
                  : null
              }
              tone={PRESSURE_TONE[indicators.pressure_level] || 'default'}
              testId="live-territorial-tile-pressure"
            />
            <StatTile
              label="Conversión ofensiva"
              value={indicators.conversion_level || '—'}
              sub={
                indicators.conversion_score != null
                  ? `${indicators.conversion_score.toFixed(0)}/100`
                  : null
              }
              tone={
                indicators.conversion_level === 'ALTA'
                  ? 'emerald'
                  : indicators.conversion_level === 'MEDIA'
                  ? 'amber'
                  : 'slate'
              }
              testId="live-territorial-tile-conversion"
            />
          </div>

          {/* Corner Intelligence card — always shows the score (per user
              spec), but the recommendation/market candidates only surface
              once the score crosses the threshold. */}
          <div
            className="rounded-lg border border-border/50 bg-white/[0.02] px-3 py-2.5 space-y-2"
            data-testid="live-corner-card"
          >
            <div className="flex items-center justify-between gap-2 flex-wrap">
              <div className="flex items-center gap-2">
                <Flag className="h-3.5 w-3.5 text-violet-300" />
                <span className="text-[12px] font-medium">
                  Corner Intelligence
                </span>
                {corner?.side_team ? (
                  <span className="text-[10px] text-muted-foreground">
                    · {corner.side_team}
                  </span>
                ) : null}
              </div>
              <div className="flex items-center gap-2">
                <span
                  className="font-mono tabular-nums text-[12px] font-semibold"
                  data-testid="live-corner-score"
                >
                  {cornerScore.toFixed(0)}/100
                </span>
                <span
                  className={`text-[10px] px-1.5 py-0.5 rounded border ${
                    cornerSurfaced
                      ? 'bg-violet-500/12 border-violet-500/35 text-violet-200'
                      : 'bg-slate-500/8 border-slate-500/25 text-muted-foreground'
                  }`}
                >
                  {cornerSurfaced
                    ? corner?.state?.replace(/_/g, ' ').toLowerCase()
                    : 'interno'}
                </span>
              </div>
            </div>

            {/* Surface gate */}
            {cornerSurfaced ? (
              <div
                className="rounded-md border border-violet-500/30 bg-violet-500/8 px-2.5 py-1.5 text-[11px] text-violet-100 leading-snug"
                data-testid="live-corner-surfaced"
              >
                <div className="flex items-center gap-1.5 mb-0.5 font-medium">
                  <Sparkles className="h-3 w-3" />
                  Mercado alternativo detectado
                </div>
                {corner?.narrative_es}
              </div>
            ) : (
              <div className="flex items-center justify-between gap-2 text-[10.5px] text-muted-foreground">
                <span>
                  Score por debajo del umbral ({cornerThreshold}). Recomendación
                  interna no visible.
                </span>
                <button
                  type="button"
                  className="inline-flex items-center gap-1 text-[10px] text-cyan-300/90 hover:text-cyan-200"
                  onClick={() => setShowInternalCorner((v) => !v)}
                  data-testid="live-corner-toggle-internal"
                >
                  {showInternalCorner ? (
                    <>
                      <EyeOff className="h-3 w-3" />
                      Ocultar interno
                    </>
                  ) : (
                    <>
                      <Eye className="h-3 w-3" />
                      Ver interno
                    </>
                  )}
                </button>
              </div>
            )}

            {/* Reasons (always visible if any) */}
            {Array.isArray(corner?.reasons) && corner.reasons.length > 0 ? (
              <ul className="text-[10.5px] text-muted-foreground space-y-0.5">
                {corner.reasons.slice(0, 3).map((r, i) => (
                  <li key={i} className="flex items-start gap-1.5">
                    <span className="h-1 w-1 rounded-full bg-current mt-1.5 shrink-0" />
                    <span>{r}</span>
                  </li>
                ))}
              </ul>
            ) : null}

            {/* Internal preview of candidates when user explicitly toggled it */}
            {!cornerSurfaced && showInternalCorner && Array.isArray(corner?.market_candidates) && corner.market_candidates.length > 0 ? (
              <ul className="space-y-1" data-testid="live-corner-internal-candidates">
                {corner.market_candidates.map((m, i) => (
                  <MarketRow
                    key={i}
                    market={m}
                    index={i}
                    testIdPrefix="live-corner-internal"
                  />
                ))}
              </ul>
            ) : null}
          </div>

          {/* Ranking dinámico de mercados live */}
          {ranked.length > 0 ? (
            <div
              className="rounded-lg border border-border/50 bg-white/[0.02] px-3 py-2.5 space-y-2"
              data-testid="live-ranking-root"
            >
              <div className="flex items-center justify-between">
                <span className="text-[11px] uppercase tracking-wide text-muted-foreground flex items-center gap-1.5">
                  <Gauge className="h-3 w-3" />
                  Ranking de mercados live
                </span>
                <span className="text-[10px] text-muted-foreground tabular-nums">
                  {ranked.length} mercados
                </span>
              </div>
              <ul className="space-y-1">
                {ranked.slice(0, 6).map((m, i) => (
                  <MarketRow
                    key={`${m.market}-${i}`}
                    market={m}
                    index={i}
                    testIdPrefix="live-ranking"
                  />
                ))}
              </ul>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}

export default LiveTerritorialControlPanel;
