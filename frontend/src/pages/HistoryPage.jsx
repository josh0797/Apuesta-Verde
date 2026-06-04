import { useEffect, useState, useCallback, useMemo } from 'react';
import { useI18n } from '@/lib/i18n';
import { api } from '@/lib/api';
import { Skeleton } from '@/components/ui/skeleton';
import { Badge } from '@/components/ui/badge';
import { tierClass, humanizeSelection } from '@/lib/format';
import { BadgeCheck, ThumbsDown, Equal, Clock, Download, DollarSign, TrendingUp, TrendingDown, Loader2, Trophy, ShieldAlert, Activity } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { WinRateChart } from '@/components/WinRateChart';
import MLBCalibrationPanel from '@/components/MLBCalibrationPanel';
import { toast } from 'sonner';

// ── Sport tab definitions ───────────────────────────────────────────
// Each tab maps to the canonical sport key sent to the backend filter,
// PLUS the list of aliases the engine writes into pick_tracking
// (e.g. football <-> soccer, baseball <-> mlb). The "all" tab keeps
// the legacy mixed view for backward compatibility.
const SPORT_TABS = [
  { id: 'all',        label: 'Todos',      apiKey: null,         aliases: null,                                badge: null,         icon: Activity },
  { id: 'baseball',   label: 'MLB',        apiKey: 'baseball',   aliases: ['baseball', 'mlb'],                 badge: 'MLB',        icon: Trophy },
  { id: 'football',   label: 'Football',   apiKey: 'football',   aliases: ['football', 'soccer', null, ''],     badge: 'Football',   icon: ShieldAlert },
  { id: 'basketball', label: 'Basketball', apiKey: 'basketball', aliases: ['basketball', 'nba'],               badge: 'Basketball', icon: Trophy },
];

function matchesSport(row, tabAliases) {
  if (!tabAliases) return true;
  const s = (row?.sport || '').toLowerCase();
  // Legacy rows without sport are treated as football.
  if (s === '' && tabAliases.includes(null)) return true;
  return tabAliases.includes(s);
}

function formatMatchDate(row, lang) {
  // Prefer the canonical match_date (event date), fall back to kickoff_iso,
  // then to tracked_at so legacy rows still render something.
  const iso = row?.match_date || row?.kickoff_iso || row?.tracked_at;
  if (!iso) return '—';
  try {
    const d = new Date(iso.length === 10 ? `${iso}T12:00:00Z` : iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString(lang === 'es' ? 'es-ES' : 'en-US', {
      day: '2-digit', month: 'short', year: 'numeric',
    });
  } catch {
    return iso;
  }
}

function finalScoreDisplay(row) {
  const fs = row?.final_score;
  if (!fs) return null;
  if (fs.display) return fs.display;
  if (fs.home !== null && fs.home !== undefined && fs.away !== null && fs.away !== undefined) {
    return `${fs.home}-${fs.away}`;
  }
  return null;
}

function dedupRows(rows) {
  // Defensive dedup matching the backend rule: same canonical pick_uid
  // OR same (sport, anchor_id, market, selection, line) tuple. Keeps the
  // first occurrence (the API already sorts by tracked_at desc, so this
  // is the most recent row).
  const seenUid = new Set();
  const seenKey = new Set();
  const out = [];
  for (const row of rows || []) {
    const uid = row?.pick_uid || row?.pick_id;
    const anchor = row?.game_pk || row?.match_id || '';
    const key = [
      (row?.sport || 'football').toLowerCase(),
      String(anchor),
      (row?.market || '').toLowerCase(),
      (row?.selection || '').toLowerCase(),
      row?.line ?? '',
    ].join('|');
    if (uid && seenUid.has(uid)) continue;
    if (seenKey.has(key)) continue;
    if (uid) seenUid.add(uid);
    seenKey.add(key);
    out.push(row);
  }
  return out;
}

export default function HistoryPage() {
  const { t, lang } = useI18n();
  const [stats, setStats] = useState(null);
  const [tracked, setTracked] = useState([]);
  const [timeline, setTimeline] = useState([]);
  const [loading, setLoading] = useState(true);
  const [stake, setStake] = useState(() => Number(localStorage.getItem('vbi_stake') || '10'));
  const [settling, setSettling] = useState({});
  const [activeSport, setActiveSport] = useState(() => localStorage.getItem('vbi_history_sport') || 'all');

  const load = useCallback(async (currentStake) => {
    setLoading(true);
    try {
      const [s, tr, tl] = await Promise.all([
        api.get('/stats/dashboard', { params: { stake: currentStake } }),
        api.get('/picks/tracked'),    // load ALL — sport filtering is client-side for snappiness
        api.get('/stats/timeline'),
      ]);
      setStats(s.data);
      setTracked(tr.data.items || []);
      setTimeline(tl.data.timeline || []);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(stake); }, [load, stake]);
  useEffect(() => { localStorage.setItem('vbi_history_sport', activeSport); }, [activeSport]);

  // Counts per sport for the tab badges — computed even while loading
  // so React hook order remains consistent across renders.
  const sportCounts = useMemo(() => {
    const out = { all: (tracked || []).length };
    for (const tab of SPORT_TABS) {
      if (!tab.aliases) continue;
      out[tab.id] = (tracked || []).filter((r) => matchesSport(r, tab.aliases)).length;
    }
    return out;
  }, [tracked]);

  const updateStake = (v) => {
    const n = Math.max(0.1, Number(v) || 10);
    setStake(n);
    localStorage.setItem('vbi_stake', String(n));
  };

  const exportCsv = async () => {
    try {
      const r = await api.get('/picks/tracked/export.csv', { responseType: 'blob' });
      const url = URL.createObjectURL(new Blob([r.data], { type: 'text/csv' }));
      const a = document.createElement('a');
      a.href = url; a.download = 'picks-tracked.csv';
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e) { toast.error('Export failed'); }
  };

  // Settle a pending pick by upserting it through the same /picks/track endpoint.
  // The server uses (user_id, match_id, pick_id) as the upsert key so we only
  // need to resend the existing fields plus the new outcome.
  const settlePick = async (row, outcome) => {
    if (!row || settling[row.pick_id]) return;
    setSettling((m) => ({ ...m, [row.pick_id]: true }));
    try {
      await api.post('/picks/track', {
        run_id: row.run_id,
        match_id: row.match_id,
        market: row.market,
        selection: row.selection,
        confidence_score: row.confidence_score || 0,
        outcome,
        odds: row.odds ?? null,
        league: row.league || null,
        match_label: row.match_label || null,
        sport: row.sport || 'football',
        // Carry the enriched fields forward so the row keeps its
        // match_date / final_score / teams after the settle upsert.
        home_team:   row.home_team   ?? null,
        away_team:   row.away_team   ?? null,
        match_date:  row.match_date  ?? null,
        kickoff_iso: row.kickoff_iso ?? null,
        game_pk:     row.game_pk     ?? null,
        line:        row.line        ?? null,
        final_score: row.final_score ?? null,
        source:      'manual',
      });
      toast.success(t.history.settledOk);
      await load(stake);
    } catch (e) {
      toast.error(e?.response?.data?.detail || t.history.settleError);
    } finally {
      setSettling((m) => {
        const next = { ...m };
        delete next[row.pick_id];
        return next;
      });
    }
  };

  if (loading) return <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8"><Skeleton className="h-32 rounded-xl mb-4" /><Skeleton className="h-64 rounded-xl" /></div>;

  const roi = stats?.roi || {};
  const profitColor = (roi.total_profit ?? 0) >= 0 ? 'emerald' : 'red';

  // ── Filter + dedup per active sport tab ──────────────────────────
  const activeTab = SPORT_TABS.find((s) => s.id === activeSport) || SPORT_TABS[0];
  const visibleRows = dedupRows(
    (tracked || []).filter((row) => matchesSport(row, activeTab.aliases)),
  );

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 md:py-8 space-y-6">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-3xl md:text-4xl font-semibold tracking-tight">{t.history.title}</h1>
          <p className="text-sm text-muted-foreground mt-1">{t.history.subtitle}</p>
        </div>
        <Button variant="secondary" size="sm" onClick={exportCsv} data-testid="history-export-csv-btn">
          <Download className="h-3.5 w-3.5 mr-1.5" />{t.history.exportCsv}
        </Button>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3" data-testid="history-kpi-strip">
        <KPI label={t.history.winRate} value={`${stats?.win_rate ?? 0}%`} accent="emerald" />
        <KPI label={t.history.settled} value={(stats?.won ?? 0) + (stats?.lost ?? 0)} />
        <KPI label={t.history.streak} value={stats?.streak ?? 0} accent="amber" />
        <KPI label={t.history.last10} value={`${(stats?.last10 || []).filter(x => x.outcome === 'won').length}/${(stats?.last10 || []).length}`} />
      </div>

      {/* ROI calculator card */}
      <section className="rounded-xl border border-border bg-card p-4 md:p-5" data-testid="roi-calculator">
        <div className="flex items-center justify-between gap-3 flex-wrap mb-3">
          <div className="text-sm font-semibold uppercase tracking-wide text-muted-foreground flex items-center gap-1.5">
            <DollarSign className="h-4 w-4" />
            {t.history.roiTitle}
          </div>
          <div className="flex items-center gap-2">
            <label htmlFor="stake-input" className="text-xs text-muted-foreground">{t.history.stakeLabel}</label>
            <Input
              id="stake-input" data-testid="stake-input"
              type="text"
              inputMode="decimal"
              pattern="[0-9]+([.,][0-9]+)?"
              value={stake}
              onChange={(e) => {
                // P5 fix: accept BOTH "10.5" and "10,5" (es-ES keyboard).
                // Strip everything but digits + single decimal sep, then
                // normalise to dot before calling updateStake().
                const v = e.target.value.replace(/[^0-9.,]/g, '').replace(',', '.');
                updateStake(v);
              }}
              className="h-8 w-24 text-right mono font-mono-tabular"
            />
          </div>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <KPI label={t.history.roiTotalWagered} value={`${(roi.total_wagered ?? 0).toFixed(2)}`} hint={`${roi.settled_with_odds || 0} ${t.history.settledWithOdds}`} />
          <KPI label={t.history.roiNetProfit} value={`${(roi.total_profit ?? 0) >= 0 ? '+' : ''}${(roi.total_profit ?? 0).toFixed(2)}`} accent={profitColor} icon={(roi.total_profit ?? 0) >= 0 ? TrendingUp : TrendingDown} />
          <KPI label={t.history.roiPct} value={`${(roi.roi_pct ?? 0) >= 0 ? '+' : ''}${(roi.roi_pct ?? 0).toFixed(2)}%`} accent={profitColor} />
          <KPI label={t.history.roiAvgWonOdds} value={(roi.avg_won_odds ?? 0).toFixed(2)} hint={`${t.history.roiAvgLostOdds}: ${(roi.avg_lost_odds ?? 0).toFixed(2)}`} />
        </div>
        {(roi.settled_with_odds ?? 0) < (roi.settled_total ?? 0) && (roi.settled_total ?? 0) > 0 && (
          <p className="text-[11px] text-muted-foreground mt-3 italic">{t.history.roiHint}</p>
        )}
      </section>

      <section className="rounded-xl border border-border bg-card p-4" data-testid="winrate-evolution-section">
        <div className="text-sm font-semibold uppercase tracking-wide text-muted-foreground mb-3">{t.history.evolution}</div>
        <WinRateChart data={timeline} />
      </section>

      <section className="rounded-xl border border-border bg-card p-4">
        <div className="text-sm font-semibold uppercase tracking-wide text-muted-foreground mb-3">{t.history.byTier}</div>
        <div className="grid sm:grid-cols-3 gap-3">
          {['Maxima', 'Alta', 'Media'].map((tier) => {
            const tierData = stats?.accuracy_by_tier?.[tier] || {};
            const tierRoi = tierData.roi_pct ?? 0;
            return (
              <div key={tier} className={`rounded-lg p-3 ${tierClass(tier)}`} data-testid={`tier-${tier}`}>
                <div className="text-[11px] uppercase opacity-80">{t.confidence[tier]}</div>
                <div className="text-2xl mono font-mono-tabular font-semibold">{tierData.rate ?? 0}%</div>
                <div className="text-[11px] opacity-80 mono font-mono-tabular">{tierData.won ?? 0}/{tierData.settled ?? 0}</div>
                <div className={`text-[11px] mono font-mono-tabular mt-1 ${tierRoi >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                  ROI: {tierRoi >= 0 ? '+' : ''}{tierRoi.toFixed(1)}%
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* MLB Negative-Binomial calibration panel — rendered when the user
          has any baseball pick tracked (won/lost/push/pending) so they can
          audit how the NB model is correcting Poisson on their slate. */}
      {sportCounts.baseball > 0 && (
        <MLBCalibrationPanel days={30} />
      )}

      {/* ── Sport tab selector ───────────────────────────────────── */}
      <section
        className="rounded-xl border border-border bg-card p-3 md:p-4"
        data-testid="history-sport-tabs-section"
      >
        <div
          className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1 pb-1"
          role="tablist"
          data-testid="history-sport-tabs"
        >
          {SPORT_TABS.map((tab) => {
            const Icon = tab.icon;
            const count = sportCounts[tab.id] ?? 0;
            const isActive = activeSport === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={isActive}
                onClick={() => setActiveSport(tab.id)}
                className={[
                  'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium',
                  'border whitespace-nowrap transition-colors',
                  isActive
                    ? 'border-cyan-500/40 bg-cyan-500/15 text-cyan-100'
                    : 'border-border bg-transparent text-muted-foreground hover:text-foreground hover:bg-secondary/40',
                ].join(' ')}
                data-testid={`history-tab-${tab.id}`}
              >
                <Icon className="h-3.5 w-3.5" />
                <span>{tab.label}</span>
                <span
                  className={[
                    'ml-1 rounded-full px-1.5 py-0.5 text-[10px] mono font-mono-tabular',
                    isActive ? 'bg-cyan-500/25 text-cyan-100' : 'bg-secondary/60 text-muted-foreground',
                  ].join(' ')}
                  data-testid={`history-tab-${tab.id}-count`}
                >
                  {count}
                </span>
              </button>
            );
          })}
        </div>
      </section>

      {visibleRows.length === 0 ? (
        <div
          className="rounded-xl border border-dashed border-border bg-card/40 p-8 text-center"
          data-testid="history-empty"
        >
          <p className="text-sm text-muted-foreground">
            {activeSport === 'all'
              ? t.history.empty
              : `No hay historial para ${activeTab.label} todavía.`}
          </p>
        </div>
      ) : (
        <>
          {/* Desktop table */}
          <div
            className="rounded-xl border border-border bg-card overflow-hidden hidden md:block"
            data-testid="history-table-desktop"
          >
            <table className="w-full text-sm">
              <thead className="bg-secondary/50">
                <tr>
                  <th className="text-left px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Fecha</th>
                  <th className="text-left px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Partido</th>
                  <th className="text-center px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Marcador</th>
                  <th className="text-left px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Mercado · Selección</th>
                  <th className="text-right px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Conf.</th>
                  <th className="text-right px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Cuota</th>
                  <th className="text-right px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Resultado</th>
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((row, i) => (
                  <HistoryRow
                    key={row.pick_uid || row.pick_id || i}
                    row={row}
                    i={i}
                    lang={lang}
                    t={t}
                    busy={!!settling[row.pick_id]}
                    onSettle={settlePick}
                  />
                ))}
              </tbody>
            </table>
          </div>

          {/* Mobile card layout */}
          <div
            className="md:hidden space-y-3"
            data-testid="history-cards-mobile"
          >
            {visibleRows.map((row, i) => (
              <HistoryCard
                key={row.pick_uid || row.pick_id || i}
                row={row}
                i={i}
                lang={lang}
                t={t}
                busy={!!settling[row.pick_id]}
                onSettle={settlePick}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// ── Sport badge helper ─────────────────────────────────────────────
function SportBadge({ sport }) {
  const s = (sport || 'football').toLowerCase();
  const cfg = {
    baseball:   { label: 'MLB',        cls: 'bg-amber-500/15 text-amber-200 border-amber-500/30' },
    mlb:        { label: 'MLB',        cls: 'bg-amber-500/15 text-amber-200 border-amber-500/30' },
    football:   { label: 'Football',   cls: 'bg-emerald-500/15 text-emerald-200 border-emerald-500/30' },
    soccer:     { label: 'Football',   cls: 'bg-emerald-500/15 text-emerald-200 border-emerald-500/30' },
    basketball: { label: 'Basketball', cls: 'bg-orange-500/15 text-orange-200 border-orange-500/30' },
    nba:        { label: 'Basketball', cls: 'bg-orange-500/15 text-orange-200 border-orange-500/30' },
  }[s] || { label: s, cls: 'bg-secondary text-muted-foreground border-border' };
  return (
    <Badge
      variant="outline"
      className={`text-[10px] uppercase tracking-wide ${cfg.cls}`}
      data-testid={`sport-badge-${s}`}
    >
      {cfg.label}
    </Badge>
  );
}

// ── Desktop row ────────────────────────────────────────────────────
function HistoryRow({ row, i, lang, t, busy, onSettle }) {
  const parts = String(row.match_label || '').split(/\s+vs\s+|\s+@\s+/i);
  const homeName = row.home_team || parts[0]?.trim() || '';
  const awayName = row.away_team || parts[1]?.trim() || '';
  const humanSelection = humanizeSelection(
    row.selection, row.market, homeName, awayName, lang, row.sport || 'football',
  );
  const isPending = (row.outcome || row.result) === 'pending';
  const fs = finalScoreDisplay(row);
  return (
    <tr
      className="border-t border-border hover:bg-white/[0.03]"
      data-testid={`tracked-row-${i}`}
    >
      <td className="px-3 py-2 align-top whitespace-nowrap text-xs text-muted-foreground mono font-mono-tabular" data-testid={`row-date-${i}`}>
        <div className="flex flex-col gap-1">
          <span>{formatMatchDate(row, lang)}</span>
          <SportBadge sport={row.sport} />
        </div>
      </td>
      <td className="px-3 py-2 align-top">
        <div className="text-foreground text-sm leading-tight">{row.match_label || `${homeName} vs ${awayName}` || row.match_id}</div>
        {row.league && (
          <div className="text-[10px] text-muted-foreground mt-0.5">{row.league}</div>
        )}
      </td>
      <td className="px-3 py-2 align-top text-center mono font-mono-tabular text-sm" data-testid={`row-score-${i}`}>
        {fs ? (
          <span className="text-foreground font-medium">{fs}</span>
        ) : (
          <span className="text-muted-foreground text-xs italic">{isPending ? 'Pendiente' : '—'}</span>
        )}
      </td>
      <td className="px-3 py-2 align-top text-muted-foreground" data-testid={`tracked-selection-${i}`}>
        <span className="text-foreground/80 font-medium">{row.market}</span>
        <span className="text-muted-foreground/80">: </span>
        <span className="text-foreground">{humanSelection}</span>
      </td>
      <td className="px-3 py-2 align-top text-right mono font-mono-tabular">{row.confidence_score}</td>
      <td className="px-3 py-2 align-top text-right mono font-mono-tabular text-muted-foreground" data-testid={`row-odds-${i}`}>
        {row.odds ? Number(row.odds).toFixed(2) : '—'}
      </td>
      <td className="px-3 py-2 align-top text-right">
        {isPending ? (
          <SettleActions row={row} i={i} t={t} busy={busy} onSettle={onSettle} />
        ) : (
          <OutcomePill outcome={row.outcome || row.result} t={t} />
        )}
      </td>
    </tr>
  );
}

// ── Mobile card ────────────────────────────────────────────────────
function HistoryCard({ row, i, lang, t, busy, onSettle }) {
  const parts = String(row.match_label || '').split(/\s+vs\s+|\s+@\s+/i);
  const homeName = row.home_team || parts[0]?.trim() || '';
  const awayName = row.away_team || parts[1]?.trim() || '';
  const humanSelection = humanizeSelection(
    row.selection, row.market, homeName, awayName, lang, row.sport || 'football',
  );
  const isPending = (row.outcome || row.result) === 'pending';
  const fs = finalScoreDisplay(row);
  return (
    <div
      className="rounded-xl border border-border bg-card p-3 space-y-2"
      data-testid={`tracked-card-${i}`}
    >
      <div className="flex items-center justify-between gap-2">
        <SportBadge sport={row.sport} />
        <span className="text-[11px] text-muted-foreground mono font-mono-tabular">
          {formatMatchDate(row, lang)}
        </span>
      </div>
      <div className="text-sm font-medium text-foreground leading-tight">
        {row.match_label || `${homeName} vs ${awayName}` || row.match_id}
      </div>
      <div className="flex items-center justify-between gap-2 flex-wrap text-xs">
        <div className="text-muted-foreground">
          <span className="text-foreground/80 font-medium">{row.market}:</span>{' '}
          <span className="text-foreground">{humanSelection}</span>
        </div>
        <div className="flex items-center gap-2 mono font-mono-tabular">
          <span className="text-muted-foreground">Conf.</span>
          <span className="text-foreground">{row.confidence_score}</span>
          {row.odds ? (
            <>
              <span className="text-muted-foreground ml-1">@</span>
              <span className="text-foreground">{Number(row.odds).toFixed(2)}</span>
            </>
          ) : null}
        </div>
      </div>
      <div className="flex items-center justify-between gap-2 pt-1 border-t border-border">
        <span className="text-sm mono font-mono-tabular text-foreground/90">
          {fs ?? (isPending ? <span className="text-muted-foreground italic text-xs">Pendiente</span> : '—')}
        </span>
        {isPending ? (
          <SettleActions row={row} i={i} t={t} busy={busy} onSettle={onSettle} />
        ) : (
          <OutcomePill outcome={row.outcome || row.result} t={t} />
        )}
      </div>
    </div>
  );
}

function SettleActions({ row, i, t, busy, onSettle }) {
  if (busy) {
    return <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" data-testid={`settle-busy-${i}`} />;
  }
  return (
    <div className="inline-flex items-center gap-1 justify-end" data-testid={`settle-actions-${i}`}>
      <Button
        size="sm" variant="ghost"
        onClick={() => onSettle(row, 'won')}
        className="h-7 px-2 text-emerald-300 hover:text-emerald-200 hover:bg-emerald-500/10"
        data-testid={`settle-won-${i}`}
        title={t.history.markWon}
      >
        <BadgeCheck className="h-3.5 w-3.5" />
      </Button>
      <Button
        size="sm" variant="ghost"
        onClick={() => onSettle(row, 'lost')}
        className="h-7 px-2 text-red-300 hover:text-red-200 hover:bg-red-500/10"
        data-testid={`settle-lost-${i}`}
        title={t.history.markLost}
      >
        <ThumbsDown className="h-3.5 w-3.5" />
      </Button>
      <Button
        size="sm" variant="ghost"
        onClick={() => onSettle(row, 'push')}
        className="h-7 px-2 text-muted-foreground hover:text-foreground hover:bg-white/[0.06]"
        data-testid={`settle-push-${i}`}
        title={t.history.markPush}
      >
        <Equal className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}

function KPI({ label, value, accent, icon: Icon, hint }) {
  const cls = accent === 'emerald' ? 'border-emerald-500/30 bg-emerald-500/5 text-emerald-300' : accent === 'red' ? 'border-red-500/30 bg-red-500/5 text-red-300' : accent === 'amber' ? 'border-amber-500/30 bg-amber-500/5 text-amber-300' : 'border-border bg-card';
  return (
    <div className={`rounded-lg border p-3 ${cls}`}>
      <div className="text-[11px] uppercase tracking-wide opacity-80 flex items-center gap-1.5">
        {Icon && <Icon className="h-3 w-3" />}
        {label}
      </div>
      <div className="text-2xl mono font-mono-tabular font-semibold mt-0.5">{value}</div>
      {hint && <div className="text-[10px] opacity-70 mt-0.5 mono font-mono-tabular">{hint}</div>}
    </div>
  );
}

function OutcomePill({ outcome, t }) {
  if (outcome === 'won') return <span className="inline-flex items-center gap-1 text-emerald-300 text-xs"><BadgeCheck className="h-3.5 w-3.5" />{t.history.markWon}</span>;
  if (outcome === 'lost') return <span className="inline-flex items-center gap-1 text-red-300 text-xs"><ThumbsDown className="h-3.5 w-3.5" />{t.history.markLost}</span>;
  if (outcome === 'push') return <span className="inline-flex items-center gap-1 text-muted-foreground text-xs"><Equal className="h-3.5 w-3.5" />{t.history.markPush}</span>;
  return <span className="inline-flex items-center gap-1 text-cyan-300 text-xs"><Clock className="h-3.5 w-3.5" />{t.history.outcomePending}</span>;
}
