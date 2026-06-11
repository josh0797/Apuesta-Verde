/**
 * FootballContextTrendCard — Phase F57.
 *
 * Observe-only card that surfaces the football Context + Trend signals:
 *   • Squad Disruption (with source URLs)
 *   • Recent Form Streaks
 *   • Corners Trend (last-10 vs last-5)
 *   • Protected Goals Trend
 *   • Missed-match Rescue
 *
 * The card NEVER modifies the engine pick — it shows complementary
 * intelligence. Footer makes the observe-only nature explicit.
 */
import { useEffect, useState } from 'react';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { AlertTriangle, Activity, Flag, Goal, LifeBuoy, ExternalLink, TrendingUp } from 'lucide-react';
import { api } from '@/lib/api';

function bucketTone(bucket) {
  if (bucket === 'HIGH')   return 'bg-amber-500/15 text-amber-300 border-amber-500/40';
  if (bucket === 'MEDIUM') return 'bg-sky-500/15 text-sky-300 border-sky-500/40';
  return 'bg-muted text-muted-foreground border-border';
}

function tierTone(tier) {
  if (tier === 'VALUE') return 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40';
  if (tier === 'WATCH') return 'bg-sky-500/15 text-sky-300 border-sky-500/40';
  return 'bg-muted text-muted-foreground border-border';
}

function SectionHeader({ icon: Icon, title, badge }) {
  return (
    <div className="flex items-center justify-between mb-2">
      <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
        <Icon className="w-4 h-4 text-muted-foreground" />
        <span>{title}</span>
      </div>
      {badge}
    </div>
  );
}

function SquadDisruptionBlock({ team, payload }) {
  if (!payload || !payload.available) return null;
  const score = payload.squad_disruption_score || 0;
  const bucket = payload.bucket || 'LOW';
  if (bucket === 'LOW') return null;
  return (
    <div
      className="rounded-lg border border-border bg-background/50 p-3"
      data-testid={`football-context-disruption-${team}`}
    >
      <SectionHeader
        icon={AlertTriangle}
        title={`Disrupción de plantel · ${team}`}
        badge={
          <Badge variant="outline" className={`text-[10px] ${bucketTone(bucket)}`}>
            {bucket} · {score}
          </Badge>
        }
      />
      <div className="flex flex-wrap gap-1 mb-2">
        {(payload.reason_codes || []).map((rc) => (
          <Badge key={rc} variant="outline" className="text-[10px] font-mono">
            {rc}
          </Badge>
        ))}
      </div>
      <ul className="space-y-1.5">
        {(payload.evidence_sources || []).slice(0, 4).map((ev, i) => (
          <li key={i} className="text-xs flex items-start gap-2">
            <ExternalLink className="w-3 h-3 mt-0.5 text-muted-foreground flex-shrink-0" />
            <a
              href={ev.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sky-400 hover:underline truncate"
              data-testid={`football-context-source-${team}-${i}`}
            >
              {ev.title}
            </a>
            <span className="text-muted-foreground text-[10px] ml-auto flex-shrink-0">
              {ev.source_name}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function FormStreaksBlock({ team, payload }) {
  if (!payload || !payload.available) return null;
  const streaks = payload.form_streaks || [];
  if (!streaks.length) return null;
  return (
    <div
      className="rounded-lg border border-border bg-background/50 p-3"
      data-testid={`football-context-form-${team}`}
    >
      <SectionHeader
        icon={Activity}
        title={`Racha reciente · ${team}`}
      />
      <div className="flex flex-wrap gap-1">
        {streaks.map((s) => (
          <Badge key={s} variant="outline" className="text-[10px] font-mono">
            {s}
          </Badge>
        ))}
      </div>
    </div>
  );
}

function CornersBlock({ team, payload }) {
  if (!payload || !payload.available || !payload.corners_signal) return null;
  return (
    <div
      className="rounded-lg border border-border bg-background/50 p-3"
      data-testid={`football-context-corners-${team}`}
    >
      <SectionHeader
        icon={Flag}
        title={`Corners · ${team}`}
        badge={
          <Badge variant="outline" className="text-[10px] bg-sky-500/15 text-sky-300 border-sky-500/40">
            conf {payload.confidence}
          </Badge>
        }
      />
      <div className="text-xs text-muted-foreground mb-2">
        Prom. L10: <span className="text-foreground font-mono">{payload.avg_for_last_10 ?? '—'}</span>
        <span className="mx-2">·</span>
        Prom. L5: <span className="text-foreground font-mono">{payload.avg_for_last_5 ?? '—'}</span>
      </div>
      {payload.recommended_market && (
        <div className="text-sm font-semibold text-emerald-300 mb-2">
          {payload.recommended_market}
        </div>
      )}
      <div className="flex flex-wrap gap-1">
        {(payload.reason_codes || []).map((rc) => (
          <Badge key={rc} variant="outline" className="text-[10px] font-mono">
            {rc}
          </Badge>
        ))}
      </div>
    </div>
  );
}

function ProtectedGoalsBlock({ payload }) {
  if (!payload || !payload.goals_trend_signal) return null;
  return (
    <div
      className="rounded-lg border border-border bg-background/50 p-3"
      data-testid="football-context-protected-goals"
    >
      <SectionHeader
        icon={Goal}
        title="Tendencia de goles (protegida)"
        badge={
          <Badge variant="outline" className="text-[10px] bg-sky-500/15 text-sky-300 border-sky-500/40">
            conf {payload.confidence}
          </Badge>
        }
      />
      {payload.recommended_market && (
        <div className="text-sm font-semibold text-emerald-300 mb-2">
          Recomendado: {payload.recommended_market}
        </div>
      )}
      {payload.avoid_markets && payload.avoid_markets.length > 0 && (
        <div className="text-xs text-muted-foreground mb-2">
          Evitar:{' '}
          {payload.avoid_markets.map((m) => (
            <span key={m} className="text-rose-300 font-mono mr-2">{m}</span>
          ))}
        </div>
      )}
      <div className="flex flex-wrap gap-1">
        {(payload.reason_codes || []).map((rc) => (
          <Badge key={rc} variant="outline" className="text-[10px] font-mono">
            {rc}
          </Badge>
        ))}
      </div>
    </div>
  );
}

function RescueBlock({ payload }) {
  if (!payload || !payload.rescued_by_context_trend) return null;
  return (
    <div
      className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-3"
      data-testid="football-context-rescue"
    >
      <SectionHeader
        icon={LifeBuoy}
        title="Partido rescatado"
        badge={
          <Badge variant="outline" className="text-[10px] bg-emerald-500/20 text-emerald-300 border-emerald-500/40">
            {payload.original_engine_status}
          </Badge>
        }
      />
      <div className="text-xs text-foreground">{payload.rescue_reason}</div>
    </div>
  );
}

function RecommendedMarketsBlock({ markets }) {
  if (!markets || !markets.length) return null;
  return (
    <div
      className="rounded-lg border border-border bg-background/50 p-3"
      data-testid="football-context-recommended-markets"
    >
      <SectionHeader icon={TrendingUp} title="Mercados sugeridos (observe-only)" />
      <ul className="space-y-2">
        {markets.map((m, i) => (
          <li
            key={`${m.market_code}-${i}`}
            className="flex items-center justify-between text-sm"
            data-testid={`football-context-market-${i}`}
          >
            <div className="flex flex-col">
              <span className="text-foreground font-medium">{m.market}</span>
              <div className="flex flex-wrap gap-1 mt-1">
                {(m.reason_codes || []).slice(0, 4).map((rc) => (
                  <Badge key={rc} variant="outline" className="text-[10px] font-mono">
                    {rc}
                  </Badge>
                ))}
              </div>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              <Badge variant="outline" className="text-[10px]">
                conf {m.confidence}
              </Badge>
              <Badge variant="outline" className="text-[10px]">
                frag {m.fragility}
              </Badge>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function FootballContextTrendCard({ sport, homeName, awayName, matchId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);

  const isFootball = (sport || '').toLowerCase() === 'football';

  useEffect(() => {
    if (!isFootball || !homeName || !awayName) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      setErr(null);
      try {
        const resp = await api.get('/football/context-trend', {
          params: {
            home_team: homeName,
            away_team: awayName,
            match_id: matchId || undefined,
          },
        });
        if (!cancelled) setData(resp.data);
      } catch (e) {
        if (!cancelled) setErr(e?.message || 'failed');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [isFootball, homeName, awayName, matchId]);

  if (!isFootball) return null;

  if (loading) {
    return (
      <Card className="p-5 bg-card border-border" data-testid="football-context-trend-card-loading">
        <div className="flex items-center gap-2 mb-4">
          <Activity className="w-4 h-4 text-sky-400" />
          <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            Contexto y Tendencias
          </h3>
        </div>
        <Skeleton className="h-20 w-full mb-2" />
        <Skeleton className="h-16 w-full" />
      </Card>
    );
  }

  if (err || !data || !data.available) {
    return null;
  }

  const hasSomething =
    (data.squad_disruption?.home?.bucket && data.squad_disruption.home.bucket !== 'LOW') ||
    (data.squad_disruption?.away?.bucket && data.squad_disruption.away.bucket !== 'LOW') ||
    (data.form_streaks?.home?.form_streaks?.length > 0) ||
    (data.form_streaks?.away?.form_streaks?.length > 0) ||
    data.corners_trend?.home?.corners_signal ||
    data.corners_trend?.away?.corners_signal ||
    data.protected_goals_trend?.goals_trend_signal ||
    data.missed_match_rescue?.rescued_by_context_trend ||
    (data.recommended_markets || []).length > 0;

  if (!hasSomething) {
    return null;
  }

  return (
    <Card
      className="p-5 bg-card border-border"
      data-testid="football-context-trend-card"
    >
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Activity className="w-4 h-4 text-sky-400" />
          <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            Contexto y Tendencias
          </h3>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="outline" className={`text-[10px] ${bucketTone(data.context_bucket)}`}>
            Contexto · {data.context_bucket} · {data.context_score}
          </Badge>
          <Badge variant="outline" className={`text-[10px] ${bucketTone(data.trend_bucket)}`}>
            Tendencia · {data.trend_bucket} · {data.trend_score}
          </Badge>
        </div>
      </div>

      {data.narrative_es && (
        <p
          className="text-xs text-muted-foreground italic mb-4"
          data-testid="football-context-narrative"
        >
          {data.narrative_es}
        </p>
      )}

      <div className="grid sm:grid-cols-2 gap-3">
        <SquadDisruptionBlock team={homeName} payload={data.squad_disruption?.home} />
        <SquadDisruptionBlock team={awayName} payload={data.squad_disruption?.away} />
        <FormStreaksBlock team={homeName} payload={data.form_streaks?.home} />
        <FormStreaksBlock team={awayName} payload={data.form_streaks?.away} />
        <CornersBlock team={homeName} payload={data.corners_trend?.home} />
        <CornersBlock team={awayName} payload={data.corners_trend?.away} />
      </div>

      <div className="mt-3 space-y-3">
        <ProtectedGoalsBlock payload={data.protected_goals_trend} />
        <RescueBlock payload={data.missed_match_rescue} />
        <RecommendedMarketsBlock markets={data.recommended_markets} />
      </div>

      <div className="mt-4 pt-3 border-t border-border/50 text-[10px] text-muted-foreground flex items-center justify-between">
        <span>Observe-only · No modifica el pick del engine</span>
        <span className="font-mono">{data.engine_version}</span>
      </div>
    </Card>
  );
}

export default FootballContextTrendCard;
