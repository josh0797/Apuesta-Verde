/**
 * useLiveMatchDetail
 * ==================
 * Hook that fetches live state for an MLB match and polls every 30s while
 * the game is in progress. Stops polling automatically when the state
 * transitions to `final` so we don't spam the backend.
 *
 * States returned (mirrors the backend contract):
 *   • loading           — first request in flight, no data yet
 *   • live-data-ready   — score + inning available
 *   • live-data-partial — only score available, missing inning/outs
 *   • final             — game ended
 *   • no-live-data      — game hasn't started yet OR endpoint unavailable
 *
 * The caller renders the appropriate banner / fallback based on `state`.
 *
 * @param matchId    Stringified gamePk (e.g. "824832").
 * @param sport      Match sport — live refresh only fires for "baseball".
 * @param opts.enabled  Toggle polling off (e.g. when modal is hidden).
 * @param opts.intervalMs  Polling cadence — defaults to 30_000.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';

const DEFAULT_INTERVAL_MS = 30_000;

export function useLiveMatchDetail(matchId, sport, opts = {}) {
  const { enabled = true, intervalMs = DEFAULT_INTERVAL_MS } = opts;
  const [live, setLive]       = useState(null);
  const [state, setState]     = useState('loading');
  const [error, setError]     = useState(null);
  const [lastFetch, setLast]  = useState(null);
  const [refreshing, setRefr] = useState(false);
  const timerRef = useRef(null);

  const stopTimer = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const refresh = useCallback(async () => {
    if (!matchId) return;
    // Sport gate — we only have a live fetcher for baseball today.
    // For other sports we synthesise a `no-live-data` snapshot so the
    // caller can render a sport-appropriate fallback (football/basketball
    // already have their own dedicated panels).
    if (sport && sport !== 'baseball') {
      setLive(null);
      setState('no-live-data');
      setError(null);
      return;
    }
    setRefr(true);
    try {
      const r = await api.get(`/matches/${matchId}/live-refresh`);
      const d = r.data || {};
      setLive(d);
      setState(d.state || 'no-live-data');
      setError(null);
      setLast(d.fetched_at || new Date().toISOString());
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'live refresh failed');
      // Don't blow away last-known-good data on a transient failure —
      // keep showing the previous snapshot but flag the error.
      if (state === 'loading') setState('no-live-data');
    } finally {
      setRefr(false);
    }
  }, [matchId, sport, state]);

  // Initial fetch + interval management. We stop polling once the game
  // is final so a tab left open over a weekend doesn't keep hammering.
  useEffect(() => {
    if (!enabled || !matchId) return undefined;
    refresh();
    return () => stopTimer();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matchId, sport, enabled]);

  useEffect(() => {
    stopTimer();
    if (!enabled) return undefined;
    // Only poll when the game is actually live. Pre-game and final
    // states don't need a tick — the user can hit Refresh manually.
    if (state === 'live-data-ready' || state === 'live-data-partial') {
      timerRef.current = setInterval(() => { refresh(); }, intervalMs);
    }
    return () => stopTimer();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, enabled, intervalMs]);

  return {
    live,
    state,
    error,
    lastFetch,
    refreshing,
    refresh,
    isPolling: timerRef.current != null,
  };
}

export default useLiveMatchDetail;
