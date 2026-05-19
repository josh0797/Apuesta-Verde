import { motion } from 'framer-motion';
import { Link } from 'react-router-dom';
import { Clock, AlertOctagon, ShieldCheck } from 'lucide-react';
import { useI18n } from '@/lib/i18n';
import { formatDateTime, formatOdd, tierClass, confidenceTier } from '@/lib/format';
import { ConfidenceMeter } from './ConfidenceMeter';
import { MotivationBadge } from './MotivationBadge';
import { FreshnessBadge } from './FreshnessBadge';
import { LivePulse } from './LivePulse';
import { LineMovement } from './LineMovement';

export function MatchCard({ pick, idx = 0 }) {
  const { lang, t } = useI18n();
  const m = pick;
  const tier = confidenceTier(m.recommendation?.confidence_score || 0);
  const tierCls = tierClass(tier);
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, delay: Math.min(idx * 0.04, 0.4) }}
      className="card-glow rounded-xl border border-border/80 bg-card p-4 md:p-5 flex flex-col gap-3"
      data-testid={`pick-card-${m.match_id}`}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            {m.is_live ? <LivePulse minute={m.live_minute} label={t.match.livePill} /> : (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md border border-cyan-500/30 bg-cyan-500/10 text-cyan-200 text-[11px] font-semibold">
                <Clock className="h-3 w-3" />{t.match.upcomingPill}
              </span>
            )}
            <span className="text-[11px] text-muted-foreground">{m.league}</span>
            <span className="text-[11px] text-muted-foreground">·</span>
            <span className="text-[11px] text-muted-foreground mono font-mono-tabular">{formatDateTime(m.kickoff_iso, lang)}</span>
          </div>
          <Link to={`/match/${m.match_id}`} className="text-lg font-semibold leading-tight hover:text-cyan-300 transition-colors" data-testid={`pick-title-${m.match_id}`}>
            {m.match_label}
          </Link>
          <div className="flex items-center gap-2 mt-1">
            <MotivationBadge level={m.motivation?.home?.level || 3} lang={lang} tooltip={`Home: ${m.motivation?.home?.reason || ''}`} />
            <span className="text-[10px] text-muted-foreground">vs</span>
            <MotivationBadge level={m.motivation?.away?.level || 3} lang={lang} tooltip={`Away: ${m.motivation?.away?.reason || ''}`} />
          </div>
        </div>
        <div className="flex flex-col items-end gap-2 shrink-0">
          <FreshnessBadge status={m.data_freshness?.odds || 'fresh'} kind="odds" />
          <FreshnessBadge status={m.data_freshness?.context || 'fresh'} kind="ctx" />
        </div>
      </div>

      {/* Recommendation */}
      <div className="rounded-lg bg-secondary/40 border border-border p-3 flex flex-col sm:flex-row sm:items-center gap-3">
        <div className="flex-1 min-w-0">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{t.match.recommendation}</div>
          <div className="text-sm font-semibold mt-0.5 flex flex-wrap items-center gap-2">
            <span className="px-2 py-0.5 rounded-md bg-emerald-500/10 text-emerald-200 border border-emerald-500/25 text-[11px]">{m.recommendation?.market}</span>
            <span className="text-foreground">{m.recommendation?.selection}</span>
          </div>
          <div className="text-xs text-muted-foreground mt-1 flex items-center gap-3">
            <span className="mono font-mono-tabular">{t.match.oddsRange}: <span className="text-foreground">{m.recommendation?.odds_range || '—'}</span></span>
            <LineMovement movement={m.key_data?.line_movement} />
          </div>
        </div>
        <ConfidenceMeter score={m.recommendation?.confidence_score || 0} testId={`pick-conf-${m.match_id}`} />
      </div>

      {/* Reasoning */}
      {m.reasoning && (
        <p className="text-sm text-muted-foreground leading-relaxed border-l-2 border-cyan-500/40 pl-3">
          {m.reasoning}
        </p>
      )}

      {/* Risks + cash-out */}
      <div className="flex flex-wrap items-center gap-2">
        {(m.risks || []).slice(0, 3).map((r, i) => (
          <span key={i} className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] rounded-md bg-red-500/10 text-red-200 border border-red-500/25">
            <AlertOctagon className="h-3 w-3" />{r}
          </span>
        ))}
        {m.cash_out && (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] rounded-md bg-cyan-500/10 text-cyan-200 border border-cyan-500/25 ml-auto">
            <ShieldCheck className="h-3 w-3" />{t.match.cashOut}: {m.cash_out}
          </span>
        )}
      </div>
    </motion.div>
  );
}
