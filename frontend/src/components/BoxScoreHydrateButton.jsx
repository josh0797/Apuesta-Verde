/**
 * BoxScoreHydrateButton — manual trigger for the Four-Factors hydration.
 *
 * Phase 41 (P2 follow-up). Renders a small button that POSTs to
 * `/api/analysis/box-scores/hydrate` for a given basketball / baseball
 * match. The backend fetches per-game box-scores via API-Sports +
 * fallback (Balldontlie for NBA, MLB StatsAPI for MLB) and persists
 * them as `match._box_score_games` so the next analyze() call uses
 * REAL Four Factors instead of the historical proxy.
 *
 * Props:
 *   match     — match dict (uses match.match_id + match.sport)
 *   apiClient — axios-like instance (tests mock this)
 *   lang      — 'es' | 'en' (default 'es')
 *   testId    — optional override for the button's data-testid
 *
 * Fail-soft: any backend error is surfaced via toast; the button never
 * crashes the surrounding card. After a successful hydration we show
 * the provider summary inline for 8 seconds.
 */
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Sparkles, Loader2 } from 'lucide-react';
import { toast } from 'sonner';

export function BoxScoreHydrateButton({
  match,
  apiClient,
  lang = 'es',
  testId,
}) {
  const matchId = match?.match_id;
  const sport   = (match?.sport || '').toLowerCase();
  const supported = sport === 'basketball' || sport === 'baseball';

  const [loading, setLoading] = useState(false);
  const [summary, setSummary] = useState(null);

  if (!supported || !matchId) return null;

  const hydrate = async () => {
    setLoading(true);
    setSummary(null);
    try {
      const res = await apiClient.post(
        '/analysis/box-scores/hydrate',
        { match_id: String(matchId), sport, last_n: 8 },
        { timeout: 45000 },   // box-score fetch can be slow
      );
      const data = res?.data || {};
      if (!data.ok) {
        toast.error(lang === 'en'
          ? `Hydrate failed: ${data.reason || 'unknown'}`
          : `Falló la hidratación: ${data.reason || 'desconocido'}`);
        return;
      }
      setSummary({
        home: data.home_games || 0,
        away: data.away_games || 0,
        provider: data.provider_summary || {},
      });
      toast.success(lang === 'en'
        ? `Four Factors hydrated (${data.home_games} home / ${data.away_games} away)`
        : `Four Factors hidratados (${data.home_games} local / ${data.away_games} visita)`);
      // Clear inline summary after 8s so it doesn't clutter the card.
      setTimeout(() => setSummary(null), 8000);
    } catch (err) {
      const msg = err?.response?.data?.detail || err?.message || 'Error';
      toast.error(typeof msg === 'string' ? msg : (lang === 'en' ? 'Hydrate failed' : 'Falló la hidratación'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="flex flex-wrap items-center gap-2"
      data-testid={testId || `box-score-hydrate-wrap-${matchId}`}
    >
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={hydrate}
        disabled={loading}
        className="h-7 text-[11px] border-fuchsia-500/40 hover:bg-fuchsia-500/15 text-fuchsia-200"
        data-testid={`box-score-hydrate-btn-${matchId}`}
      >
        {loading
          ? <Loader2 className="h-3 w-3 mr-1.5 animate-spin" />
          : <Sparkles className="h-3 w-3 mr-1.5" />}
        {loading
          ? (lang === 'en' ? 'Hydrating…' : 'Hidratando…')
          : (lang === 'en' ? 'Hydrate Four Factors' : 'Hidratar Four Factors')}
      </Button>
      {summary && (
        <div
          className="inline-flex items-center gap-1.5 text-[10px] opacity-80"
          data-testid={`box-score-hydrate-summary-${matchId}`}
        >
          <Badge variant="outline" className="text-[10px]">
            {lang === 'en' ? 'home' : 'local'}: {summary.home}
          </Badge>
          <Badge variant="outline" className="text-[10px]">
            {lang === 'en' ? 'away' : 'visita'}: {summary.away}
          </Badge>
          {summary.provider?.home && (
            <span className="opacity-70">via {summary.provider.home}</span>
          )}
        </div>
      )}
    </div>
  );
}

export default BoxScoreHydrateButton;
