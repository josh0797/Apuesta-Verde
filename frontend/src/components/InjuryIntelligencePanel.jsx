/**
 * InjuryIntelligencePanel
 *
 * Renders the canonical `injury_intelligence` payload returned by
 *   GET /api/matches/{id}/injury-intelligence
 *
 * Phase 1: basketball only. The component returns `null` when:
 *   - payload is missing
 *   - payload.available === false (no injuries reported / data missing)
 *
 * Defensive: every read goes through `.?` chains so a partially-shaped
 * payload (e.g. old cache) never throws.
 */
import { useEffect, useState } from 'react';
import {
  ChevronDown, ChevronUp, Activity, AlertTriangle, ShieldOff, UserMinus,
  Clock, Info, TrendingDown,
} from 'lucide-react';
import { api } from '@/lib/api';

const TIER_CLASS = {
  LOW:      'bg-emerald-500/10 text-emerald-200 border-emerald-500/30',
  MEDIUM:   'bg-amber-500/10 text-amber-200 border-amber-500/30',
  HIGH:     'bg-orange-500/10 text-orange-200 border-orange-500/30',
  CRITICAL: 'bg-rose-500/15 text-rose-200 border-rose-500/40',
};

const ROLE_LABEL = {
  superstar: 'Superestrella',
  star:      'Estrella',
  starter:   'Titular',
  rotation:  'Rotación',
  bench:     'Banco',
  unknown:   '—',
};

const STATUS_LABEL = {
  out:                 'Fuera',
  doubtful:            'Dudoso',
  questionable:        'En duda',
  probable:            'Probable',
  day_to_day:          'Día a día',
  suspended:           'Suspendido',
  minutes_restriction: 'Minutos limitados',
  rest:                'Descanso',
  unknown:             '—',
};

const STATUS_CLASS = {
  out:                 'bg-rose-500/15 text-rose-200 border-rose-500/40',
  suspended:           'bg-rose-500/15 text-rose-200 border-rose-500/40',
  doubtful:            'bg-orange-500/15 text-orange-200 border-orange-500/40',
  questionable:        'bg-amber-500/15 text-amber-200 border-amber-500/40',
  minutes_restriction: 'bg-amber-500/10 text-amber-100 border-amber-500/30',
  rest:                'bg-slate-500/15 text-slate-200 border-slate-500/30',
  probable:            'bg-emerald-500/10 text-emerald-200 border-emerald-500/30',
  day_to_day:          'bg-slate-500/10 text-slate-200 border-slate-500/30',
  unknown:             'bg-slate-500/10 text-slate-300 border-slate-500/20',
};

// Order players by impact severity (used to sort the list inside a team).
const STATUS_PRIORITY = {
  out: 5, suspended: 5, doubtful: 4, questionable: 3,
  minutes_restriction: 2, day_to_day: 1, probable: 1, rest: 1, unknown: 0,
};
const ROLE_PRIORITY = {
  superstar: 5, star: 4, starter: 3, rotation: 2, bench: 1, unknown: 0,
};

function impactKey(p) {
  return (STATUS_PRIORITY[p?.status] || 0) * 10 + (ROLE_PRIORITY[p?.role] || 0);
}

function FreshnessChip({ value }) {
  const map = {
    fresh:   { label: 'Datos recientes', cls: 'bg-emerald-500/10 text-emerald-200 border-emerald-500/30' },
    partial: { label: 'Datos parciales', cls: 'bg-amber-500/10 text-amber-200 border-amber-500/30' },
    stale:   { label: 'Datos antiguos',  cls: 'bg-rose-500/10 text-rose-200 border-rose-500/30' },
    unknown: { label: 'Sin datos',       cls: 'bg-slate-500/10 text-slate-300 border-slate-500/30' },
  }[value] || { label: '—', cls: 'bg-slate-500/10 text-slate-300 border-slate-500/30' };
  return (
    <span
      className={`text-[10px] px-2 py-0.5 rounded-full border inline-flex items-center gap-1 ${map.cls}`}
      data-testid={`injury-freshness-${value || 'unknown'}`}
    >
      <Clock className="w-3 h-3" /> {map.label}
    </span>
  );
}

function TeamSide({ side, block, lang = 'es' }) {
  const t = block || {};
  const injuries = Array.isArray(t.injuries) ? [...t.injuries] : [];
  injuries.sort((a, b) => impactKey(b) - impactKey(a));
  const impact = t.team_injury_impact || {};
  const score = t.basketball_injury_score || {};
  const tierCls = TIER_CLASS[impact.impact_tier] || TIER_CLASS.LOW;
  const adj = score.team_strength_adjustment || 0;
  return (
    <div
      className="rounded-lg border border-border/50 bg-card/40 p-3 space-y-2"
      data-testid={`injury-team-${side}`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-xs font-semibold text-foreground/95">
          <UserMinus className="w-3.5 h-3.5 text-rose-300" />
          <span className="truncate" data-testid={`injury-team-name-${side}`}>
            {t.team_name || (side === 'home' ? 'Local' : 'Visitante')}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span
            className={`text-[10px] px-2 py-0.5 rounded-full border ${tierCls}`}
            data-testid={`injury-team-tier-${side}`}
          >
            {impact.impact_tier || 'LOW'}
          </span>
          <span
            className={`text-[10px] px-2 py-0.5 rounded-full border ${adj < 0
              ? 'bg-rose-500/10 text-rose-200 border-rose-500/30'
              : 'bg-emerald-500/10 text-emerald-200 border-emerald-500/30'}`}
            data-testid={`injury-team-adj-${side}`}
          >
            TEAM IMPACT {adj > 0 ? `+${adj}` : adj}
          </span>
        </div>
      </div>

      {impact.summary ? (
        <div className="text-[11px] text-muted-foreground italic" data-testid={`injury-team-summary-${side}`}>
          {impact.summary}
        </div>
      ) : null}

      {injuries.length === 0 ? (
        <div className="text-[11px] text-muted-foreground" data-testid={`injury-team-empty-${side}`}>
          {lang === 'es' ? 'Sin bajas reportadas.' : 'No injuries reported.'}
        </div>
      ) : (
        <ul className="space-y-1.5">
          {injuries.map((p, i) => {
            const statusCls = STATUS_CLASS[p.status] || STATUS_CLASS.unknown;
            const tier = p.impact_tier || 'LOW';
            return (
              <li
                key={`${p.player_name || 'p'}-${i}`}
                className="flex items-center justify-between gap-2 text-[11.5px]"
                data-testid={`injury-player-${side}-${i}`}
              >
                <div className="flex flex-col min-w-0">
                  <span className="text-foreground/95 truncate" data-testid={`injury-player-name-${side}-${i}`}>
                    {p.player_name || '—'}
                  </span>
                  <span className="text-[10px] text-muted-foreground">
                    {ROLE_LABEL[p.role] || '—'}
                    {p.position ? ` · ${p.position}` : ''}
                  </span>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  <span className={`text-[10px] px-2 py-0.5 rounded-full border ${statusCls}`}
                    data-testid={`injury-player-status-${side}-${i}`}>
                    {STATUS_LABEL[p.status] || p.status}
                  </span>
                  <span className={`text-[10px] px-2 py-0.5 rounded-full border ${TIER_CLASS[tier]}`}
                    data-testid={`injury-player-tier-${side}-${i}`}>
                    {tier}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export function InjuryIntelligencePanel({ matchId, sport, lang = 'es' }) {
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState(null);
  const [open, setOpen]       = useState(true);

  useEffect(() => {
    if (!matchId) return;
    // Phase 1: only basketball is supported by the backend orchestrator.
    if (sport && String(sport).toLowerCase() !== 'basketball') return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.get(`/matches/${matchId}/injury-intelligence`)
      .then((r) => { if (!cancelled) setData(r.data); })
      .catch((e) => { if (!cancelled) setError(e?.response?.data?.detail || 'fetch_failed'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [matchId, sport]);

  // Don't render anything for non-basketball (Phase 1).
  if (sport && String(sport).toLowerCase() !== 'basketball') return null;
  if (loading || error) return null;
  if (!data || data.available !== true) return null;

  const mi = data.match_impact || {};
  const edge = data.match_injury_edge || {};
  const warnings = Array.isArray(mi.market_warnings) ? mi.market_warnings : [];
  const reasonCodes = Array.isArray(mi.reason_codes) ? mi.reason_codes : [];

  const netEdgePoints = edge.net_edge_points || 0;
  const netEdgeSide   = edge.net_edge || 'neutral';
  const edgeChipClass = (netEdgeSide === 'home' || netEdgeSide === 'away')
    ? 'bg-cyan-500/10 text-cyan-200 border-cyan-500/30'
    : 'bg-slate-500/10 text-slate-200 border-slate-500/30';

  return (
    <section
      className="rounded-xl border border-border/50 bg-card/40 p-4 space-y-3"
      data-testid="injury-intelligence-panel"
    >
      <header className="flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-foreground/95 hover:text-foreground"
          data-testid="injury-intelligence-toggle"
        >
          <Activity className="h-4 w-4 text-rose-300" />
          {lang === 'es' ? 'Inteligencia de lesiones' : 'Injury Intelligence'}
          {open ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
        </button>
        <div className="flex items-center gap-1.5">
          <FreshnessChip value={data.freshness} />
          {netEdgePoints > 0 && (
            <span
              className={`text-[10px] px-2 py-0.5 rounded-full border ${edgeChipClass}`}
              data-testid="injury-net-edge-chip"
            >
              EDGE {netEdgeSide === 'home' ? (lang === 'es' ? 'LOCAL' : 'HOME')
                : netEdgeSide === 'away' ? (lang === 'es' ? 'VISITANTE' : 'AWAY')
                : '—'} +{netEdgePoints}
            </span>
          )}
          {edge.high_volatility && (
            <span
              className="text-[10px] px-2 py-0.5 rounded-full border bg-rose-500/15 text-rose-200 border-rose-500/40 inline-flex items-center gap-1"
              data-testid="injury-high-volatility-chip"
            >
              <AlertTriangle className="w-3 h-3" /> {lang === 'es' ? 'Alta volatilidad' : 'High volatility'}
            </span>
          )}
        </div>
      </header>

      {open && (
        <div className="space-y-3">
          {/* Match-level summary */}
          {mi.summary ? (
            <div
              className="text-[12px] leading-relaxed text-foreground/90 bg-background/30 border border-border/40 rounded-md px-3 py-2"
              data-testid="injury-match-summary"
            >
              {mi.summary}
            </div>
          ) : null}

          {/* Per-team blocks */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            <TeamSide side="home" block={data.home} lang={lang} />
            <TeamSide side="away" block={data.away} lang={lang} />
          </div>

          {/* Market warnings */}
          {warnings.length > 0 && (
            <div className="space-y-1.5" data-testid="injury-market-warnings">
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
                {lang === 'es' ? 'Advertencias de mercado' : 'Market warnings'}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {warnings.map((w, i) => (
                  <span
                    key={`${w}-${i}`}
                    className="text-[10px] px-2 py-0.5 rounded-full border bg-amber-500/10 text-amber-200 border-amber-500/30 inline-flex items-center gap-1"
                    data-testid={`injury-warning-${w}`}
                  >
                    <TrendingDown className="w-3 h-3" /> {w}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Reason codes (debug-ish but useful) */}
          {reasonCodes.length > 0 && (
            <div className="flex flex-wrap gap-1 text-[10px] text-muted-foreground" data-testid="injury-reason-codes">
              {reasonCodes.map((rc, i) => (
                <span key={`${rc}-${i}`} className="font-mono">{rc}</span>
              ))}
            </div>
          )}

          {/* Confidence/Fragility adjustments */}
          {(mi.confidence_adjustment !== 0 || mi.fragility_adjustment !== 0) && (
            <div className="flex items-center gap-2 text-[11px] text-muted-foreground" data-testid="injury-adjustments">
              <Info className="w-3 h-3" />
              <span>
                {lang === 'es' ? 'Ajuste confianza:' : 'Conf. adj:'}
                <span className="text-foreground/90 font-mono ml-1">
                  {mi.confidence_adjustment > 0 ? `+${mi.confidence_adjustment}` : mi.confidence_adjustment}
                </span>
                {' · '}
                {lang === 'es' ? 'Fragilidad:' : 'Fragility:'}
                <span className="text-foreground/90 font-mono ml-1">
                  +{mi.fragility_adjustment || 0}
                </span>
              </span>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

export default InjuryIntelligencePanel;
