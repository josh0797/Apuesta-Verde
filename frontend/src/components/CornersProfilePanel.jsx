/**
 * FIX-4 — Corners Profile Panel.
 *
 * Pre-match Corners Profile renderer. Loads ``corners_profile`` (already
 * computed in background) from ``/api/football/corners/profile``.
 *
 * Critical UX principle: pre-match corners come from HISTORICAL match
 * data per team, NOT from the current fixture (which hasn't been
 * played yet). The absence of current-fixture corner stats is the
 * NORMAL state and must never be rendered as an error.
 *
 * States:
 *   • LOADING / PENDING — skeletons.
 *   • OK                 — full profile rendered.
 *   • PARTIAL            — render with banner: corner sub-market locked.
 *   • UNAVAILABLE        — explainer + reason codes.
 *   • NOT_FOUND          — gentle message + link to debug panel.
 */
import { useCallback, useEffect, useState } from 'react';
import {
  Flame, TrendingUp, TrendingDown, AlertTriangle,
  Activity, ChevronDown, ChevronUp, RefreshCcw, Info,
} from 'lucide-react';

import { api } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';

// ─────────────────────────────────────────────────────────────────────
//   Helpers
// ─────────────────────────────────────────────────────────────────────

const MOMENTUM_VISUAL = {
  BULLISH_STRONG: {
    icon: Flame,
    cls:  'border-orange-400/50 bg-orange-500/15 text-orange-100',
    es:   'Alcista fuerte',
    en:   'Strongly bullish',
  },
  BULLISH_STABLE: {
    icon: TrendingUp,
    cls:  'border-emerald-400/50 bg-emerald-500/15 text-emerald-100',
    es:   'Alcista estable',
    en:   'Steadily bullish',
  },
  BULLISH_LOSING_MOMENTUM: {
    icon: AlertTriangle,
    cls:  'border-amber-400/50 bg-amber-500/15 text-amber-100',
    es:   'Alcista perdiendo fuerza',
    en:   'Bullish but cooling',
  },
  BEARISH: {
    icon: TrendingDown,
    cls:  'border-rose-400/50 bg-rose-500/15 text-rose-100',
    es:   'Tendencia bajista',
    en:   'Bearish trend',
  },
  NEUTRAL: {
    icon: Activity,
    cls:  'border-slate-400/40 bg-slate-500/10 text-slate-200',
    es:   'Tendencia neutra',
    en:   'Flat trend',
  },
};

const LINE_PROJECTION_VISUAL = {
  FAVORABLE: 'border-emerald-400/50 bg-emerald-500/15 text-emerald-100',
  NEUTRAL:   'border-slate-400/40 bg-slate-500/10 text-slate-200',
  RISKY:     'border-rose-400/50 bg-rose-500/15 text-rose-100',
  UNKNOWN:   'border-slate-500/30 bg-slate-700/20 text-slate-300',
};

const LINE_LABEL_ES = {
  FAVORABLE: 'Favorable',
  NEUTRAL:   'Neutral',
  RISKY:     'Riesgoso',
  UNKNOWN:   'Sin dato',
};

const LINE_LABEL_EN = {
  FAVORABLE: 'Favorable',
  NEUTRAL:   'Neutral',
  RISKY:     'Risky',
  UNKNOWN:   'Unknown',
};

function fmtNum(v, decimals = 2) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  return Number.isInteger(n) ? String(n) : n.toFixed(decimals);
}

// ─────────────────────────────────────────────────────────────────────
//   Sub-components
// ─────────────────────────────────────────────────────────────────────

function TeamColumn({ side, team, lang, testId }) {
  if (!team) return null;
  const momentum = team.momentum || {};
  const visual = MOMENTUM_VISUAL[momentum.state] || MOMENTUM_VISUAL.NEUTRAL;
  const Icon = visual.icon;
  const label = lang === 'en' ? visual.en : visual.es;
  const sideLabel = (lang === 'en'
    ? (side === 'home' ? 'HOME' : 'AWAY')
    : (side === 'home' ? 'LOCAL' : 'VISITANTE'));

  return (
    <div
      className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-3 flex flex-col gap-2"
      data-testid={testId}
    >
      <div className="flex items-baseline justify-between">
        <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
          {sideLabel}
        </span>
        <span
          className="text-[12px] font-semibold text-slate-100 truncate ml-2"
          title={team.team || ''}
          data-testid={`${testId}-name`}
        >
          {team.team || '—'}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-1.5 text-center">
        <div>
          <div className="text-[9px] uppercase text-slate-400">L1</div>
          <div className="text-base font-mono font-bold text-slate-50"
               data-testid={`${testId}-l1`}>
            {fmtNum(team.l1_corners_for, 0)}
          </div>
        </div>
        <div>
          <div className="text-[9px] uppercase text-slate-400">L5</div>
          <div className="text-base font-mono font-bold text-slate-50"
               data-testid={`${testId}-l5`}>
            {fmtNum(team.l5_avg_corners_for)}
          </div>
        </div>
        <div>
          <div className="text-[9px] uppercase text-slate-400">L15</div>
          <div className="text-base font-mono font-bold text-slate-50"
               data-testid={`${testId}-l15`}>
            {fmtNum(team.l15_avg_corners_for)}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-1.5 text-[10px]">
        <div className="text-slate-400">
          {lang === 'en' ? 'For (L5)' : 'Generados L5'}:{' '}
          <span className="font-mono text-slate-100">
            {fmtNum(team.l5_avg_corners_for)}
          </span>
        </div>
        <div className="text-slate-400">
          {lang === 'en' ? 'Against (L5)' : 'Permitidos L5'}:{' '}
          <span className="font-mono text-slate-100">
            {fmtNum(team.l5_avg_corners_against)}
          </span>
        </div>
      </div>

      <div
        className={`rounded border px-2 py-1 text-[10.5px] flex items-center gap-1.5 ${visual.cls}`}
        data-testid={`${testId}-momentum`}
      >
        <Icon className="h-3 w-3 shrink-0" />
        <span className="font-semibold uppercase tracking-wider">{label}</span>
        {momentum.trend_pct !== null && momentum.trend_pct !== undefined && (
          <span className="ml-auto font-mono opacity-80">
            {momentum.trend_pct > 0 ? '+' : ''}{fmtNum(momentum.trend_pct, 1)}%
          </span>
        )}
      </div>

      <div className="text-[10px] text-slate-400">
        {lang === 'en' ? 'Sample' : 'Muestra'}:{' '}
        <span className="font-mono text-slate-200">
          {team.sample_size ?? 0}
        </span>
      </div>
    </div>
  );
}

function LineProjectionPill({ line, status, lang, testId }) {
  const cls = LINE_PROJECTION_VISUAL[status] || LINE_PROJECTION_VISUAL.UNKNOWN;
  const label = (lang === 'en' ? LINE_LABEL_EN : LINE_LABEL_ES)[status] || status;
  return (
    <div
      className={`rounded border px-2 py-1 flex items-center justify-between text-[11px] ${cls}`}
      data-testid={testId}
    >
      <span className="font-mono">Over {line}</span>
      <span className="font-semibold uppercase tracking-wide">{label}</span>
    </div>
  );
}

function DebugBreakdown({ profile, lang }) {
  const [open, setOpen] = useState(false);
  const reasonCodes = Array.isArray(profile?.reason_codes) ? profile.reason_codes : [];
  if (reasonCodes.length === 0) return null;
  return (
    <div className="border-t border-slate-700/40 pt-2 mt-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-slate-400 hover:text-slate-200"
        data-testid="corners-profile-debug-toggle"
      >
        {open ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
        {lang === 'en' ? 'Debug / reason codes' : 'Debug / códigos de razón'}
      </button>
      {open && (
        <div
          className="mt-1 flex flex-wrap gap-1"
          data-testid="corners-profile-debug-codes"
        >
          {reasonCodes.map((rc) => (
            <Badge
              key={rc}
              variant="outline"
              className="text-[10px] font-mono border-slate-600 bg-slate-800/40 text-slate-300"
            >
              {rc}
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
//   Main panel
// ─────────────────────────────────────────────────────────────────────

export function CornersProfilePanel({ matchId, lang = 'es', initialProfile = null }) {
  const [profile, setProfile]   = useState(initialProfile);
  const [status, setStatus]     = useState(initialProfile?.status || (initialProfile ? 'OK' : 'LOADING'));
  const [errorMsg, setErrorMsg] = useState(null);
  const [loading, setLoading]   = useState(!initialProfile);

  const fetchProfile = useCallback(async () => {
    if (!matchId) return;
    setLoading(true);
    setErrorMsg(null);
    try {
      const { data } = await api.get('/api/football/corners/profile', {
        params: { match_id: matchId },
      });
      if (data?.ok === false) {
        setStatus(data?.status || 'UNAVAILABLE');
        setProfile(null);
        setErrorMsg(data?.message_user || null);
      } else {
        setProfile(data?.profile || null);
        setStatus(data?.status || (data?.profile?.status) || 'UNAVAILABLE');
      }
    } catch (err) {
      setStatus('UNAVAILABLE');
      setErrorMsg(err?.message || 'Network error');
    } finally {
      setLoading(false);
    }
  }, [matchId]);

  useEffect(() => {
    if (!initialProfile && matchId) {
      fetchProfile();
    }
  }, [matchId, initialProfile, fetchProfile]);

  // Loading / PENDING state.
  if (loading || status === 'LOADING' || status === 'PENDING') {
    return (
      <div
        className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-3 flex flex-col gap-2"
        data-testid="corners-profile-panel-loading"
      >
        <div className="text-[11px] font-bold uppercase tracking-wider text-slate-300">
          {lang === 'en' ? 'Corners Profile' : 'Perfil de córners'}
        </div>
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-8 w-full" />
        <p className="text-[10px] text-slate-400 italic">
          {lang === 'en'
            ? 'Computing historical corners (L1/L5/L15)…'
            : 'Calculando córners históricos (L1/L5/L15)…'}
        </p>
      </div>
    );
  }

  // Unavailable / not found.
  if (!profile || status === 'NOT_FOUND' || status === 'UNAVAILABLE') {
    return (
      <div
        className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-3 flex flex-col gap-2"
        data-testid="corners-profile-panel-unavailable"
      >
        <div className="flex items-center justify-between">
          <span className="text-[11px] font-bold uppercase tracking-wider text-slate-300">
            {lang === 'en' ? 'Corners Profile' : 'Perfil de córners'}
          </span>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={fetchProfile}
            data-testid="corners-profile-panel-retry"
          >
            <RefreshCcw className="h-3 w-3 mr-1" />
            {lang === 'en' ? 'Retry' : 'Reintentar'}
          </Button>
        </div>
        <p className="text-[11px] text-slate-300">
          {lang === 'en'
            ? 'Historical corners not available for one or both teams. The pick can still proceed; only the corners sub-market is locked.'
            : 'Sin córners históricos suficientes para uno o ambos equipos. El pick principal puede continuar; solo el sub-mercado de córners queda bloqueado.'}
        </p>
        {errorMsg && (
          <p className="text-[10px] text-slate-400 italic">{errorMsg}</p>
        )}
        {profile?.reason_codes && <DebugBreakdown profile={profile} lang={lang} />}
      </div>
    );
  }

  // OK / PARTIAL — full render.
  const isPartial = status === 'PARTIAL' || profile.picks_blocked === true;

  return (
    <div
      className="rounded-lg border border-slate-700/60 bg-slate-900/60 p-3 flex flex-col gap-3"
      data-testid="corners-profile-panel"
    >
      {/* Header */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <span className="text-[11px] font-bold uppercase tracking-wider text-slate-200">
            {lang === 'en' ? '📊 Corners Profile' : '📊 Perfil de córners'}
          </span>
          <Badge
            variant="outline"
            className="text-[9.5px] uppercase font-mono border-slate-600 bg-slate-800/50 text-slate-300"
            data-testid="corners-profile-status"
          >
            {profile.status}
          </Badge>
          {profile.provider && (
            <Badge
              variant="outline"
              className="text-[9px] font-mono border-slate-600 bg-slate-800/30 text-slate-400"
              data-testid="corners-profile-provider"
            >
              {profile.provider}
            </Badge>
          )}
        </div>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={fetchProfile}
          data-testid="corners-profile-refresh"
        >
          <RefreshCcw className="h-3 w-3" />
        </Button>
      </div>

      {/* Pre-match expected reason banner */}
      {profile.is_pre_match && !profile.current_fixture_corners_available && (
        <div
          className="rounded border border-sky-500/30 bg-sky-500/10 px-2 py-1 flex items-start gap-1.5"
          data-testid="corners-profile-prematch-banner"
        >
          <Info className="h-3 w-3 text-sky-200 shrink-0 mt-0.5" />
          <p className="text-[10px] leading-snug text-sky-100">
            {lang === 'en'
              ? 'Pre-match analysis. Corners are derived from each team\'s historical match log — the current fixture has not been played yet.'
              : 'Análisis pre-match. Los córners provienen del historial de cada equipo — el partido actual aún no se ha jugado.'}
          </p>
        </div>
      )}

      {/* Per-team columns */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        <TeamColumn side="home" team={profile.home} lang={lang}
                     testId="corners-profile-home" />
        <TeamColumn side="away" team={profile.away} lang={lang}
                     testId="corners-profile-away" />
      </div>

      {/* Combined block */}
      <div className="rounded-md border border-slate-700/60 bg-slate-900/50 p-2 flex flex-col gap-1.5">
        <div className="flex items-baseline justify-between">
          <span className="text-[10px] uppercase tracking-wider text-slate-400">
            {lang === 'en' ? 'Combined' : 'Combinado'}
          </span>
          <span className="text-[10px] text-slate-300">
            L5: <span className="font-mono">{fmtNum(profile.combined_l5_avg)}</span>{'  ·  '}
            L15: <span className="font-mono">{fmtNum(profile.combined_l15_avg)}</span>
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-bold text-slate-100">
            {lang === 'en' ? 'Expected Corners' : 'Expected Corners'}
          </span>
          <span
            className="text-lg font-mono font-bold text-emerald-300"
            data-testid="corners-profile-expected"
          >
            {fmtNum(profile.expected_corners)}
          </span>
        </div>
      </div>

      {/* Line projections grid */}
      {profile.line_projections && Object.keys(profile.line_projections).length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-1.5">
          {Object.entries(profile.line_projections).map(([k, status]) => {
            const line = k.replace(/^over_/, '').replace(/_/g, '.');
            return (
              <LineProjectionPill
                key={k}
                line={line}
                status={status}
                lang={lang}
                testId={`corners-profile-projection-${k}`}
              />
            );
          })}
        </div>
      )}

      {/* Sub-market locked notice */}
      {isPartial && (
        <div
          className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 flex items-start gap-1.5"
          data-testid="corners-profile-submarket-locked"
        >
          <AlertTriangle className="h-3 w-3 text-amber-200 shrink-0 mt-0.5" />
          <p className="text-[10px] leading-snug text-amber-100">
            {lang === 'en'
              ? 'Corners sub-market locked: at least one team has fewer than 5 samples. The main pick is NOT affected.'
              : 'Sub-mercado de córners bloqueado: al menos un equipo tiene menos de 5 muestras. El pick principal NO se ve afectado.'}
          </p>
        </div>
      )}

      <DebugBreakdown profile={profile} lang={lang} />
    </div>
  );
}

export default CornersProfilePanel;
