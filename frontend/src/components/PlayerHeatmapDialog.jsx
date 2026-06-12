import React, { useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { MapPin, Loader2 } from 'lucide-react';

/**
 * Phase F67 — Lazy Player Heatmap viewer.
 *
 * Two modes supported:
 *
 *  A) BY EXPLICIT IDs (original): pass ``playerId`` + ``competitionId`` +
 *     ``seasonId``. The dialog fetches ``/api/football/player-heatmap/:id``.
 *
 *  B) Phase F68 — BY NAME: pass ``playerName`` + ``matchId`` (and
 *     optionally ``teamHint``, ``competitionId``, ``seasonId``). The
 *     dialog fetches ``/api/football/player-heatmap/by-name?...``, which
 *     resolves the ``pl_*`` id internally and derives competition/season
 *     from the cached match. This is the path the player props cards use
 *     because they only know names, not TheStatsAPI ids.
 *
 * Both modes return identical payload shapes.
 *
 * Props
 * -----
 *  - playerId, competitionId, seasonId  → mode A
 *  - playerName, matchId, teamHint      → mode B
 *  - playerName label                   → shown in dialog header (any mode)
 */
export function PlayerHeatmapDialog({
  playerId,
  competitionId,
  seasonId,
  playerName = 'Jugador',
  matchId,
  teamHint,
  testIdPrefix = 'player-heatmap',
}) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);

  const backendUrl = process.env.REACT_APP_BACKEND_URL;
  // Mode B activates when no explicit playerId is provided but we have a name.
  const useByName = !playerId && playerName && playerName !== 'Jugador';

  const load = async () => {
    if (data || loading) return;
    setLoading(true);
    setErr(null);
    try {
      let url;
      if (useByName) {
        const params = new URLSearchParams({ player_name: playerName });
        if (matchId)        params.set('match_id',       matchId);
        if (teamHint)       params.set('team_hint',      teamHint);
        if (competitionId)  params.set('competition_id', competitionId);
        if (seasonId)       params.set('season_id',      seasonId);
        url = `${backendUrl}/api/football/player-heatmap/by-name?${params}`;
      } else {
        url = `${backendUrl}/api/football/player-heatmap/${encodeURIComponent(playerId)}`
          + `?competition_id=${encodeURIComponent(competitionId || '')}`
          + `&season_id=${encodeURIComponent(seasonId || '')}`;
      }
      const r = await fetch(url);
      const j = await r.json();
      if (!j.available) {
        setErr(j.reason || j._error || 'No hay datos disponibles');
      } else {
        setData(j.data);
      }
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className="h-7 text-[11px] gap-1"
        onClick={() => { setOpen(true); load(); }}
        data-testid={`${testIdPrefix}-trigger`}
      >
        <MapPin className="h-3 w-3" />
        Heatmap
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-md" data-testid={`${testIdPrefix}-dialog`}>
          <DialogHeader>
            <DialogTitle>{playerName} — Mapa de calor</DialogTitle>
          </DialogHeader>
          <div className="min-h-[260px] flex items-center justify-center">
            {loading && (
              <div className="flex items-center gap-2 text-xs text-slate-400">
                <Loader2 className="h-4 w-4 animate-spin" />
                Cargando heatmap…
              </div>
            )}
            {err && !loading && (
              <div
                className="text-xs text-amber-400 max-w-xs text-center"
                data-testid={`${testIdPrefix}-error`}
              >
                {err}
              </div>
            )}
            {data && !loading && !err && (
              <HeatmapCanvas data={data} testIdPrefix={testIdPrefix} />
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}


/**
 * Pure rendering helper — interprets the heatmap payload. The shape is
 * defensive: it tries `data.points` (array of {x, y, intensity}), then
 * falls back to `data.grid` (2D matrix), then to a generic JSON dump.
 */
function HeatmapCanvas({ data, testIdPrefix }) {
  // Try points first.
  const points = Array.isArray(data?.points) ? data.points : null;
  if (points && points.length > 0) {
    return (
      <svg
        viewBox="0 0 100 65"
        className="w-full h-[260px] bg-emerald-950 rounded border border-emerald-800"
        data-testid={`${testIdPrefix}-svg`}
      >
        {/* Pitch outline */}
        <rect x="2" y="2" width="96" height="61" fill="none" stroke="rgba(255,255,255,0.25)" strokeWidth="0.3" />
        <line x1="50" y1="2" x2="50" y2="63" stroke="rgba(255,255,255,0.18)" strokeWidth="0.2" />
        <circle cx="50" cy="32.5" r="6" fill="none" stroke="rgba(255,255,255,0.18)" strokeWidth="0.2" />
        {points.map((p, i) => {
          const x = Math.max(0, Math.min(100, Number(p.x) || 0));
          const y = Math.max(0, Math.min(65,  Number(p.y) || 0));
          const intensity = Math.max(0.1, Math.min(1, Number(p.intensity) || 0.5));
          return (
            <circle
              key={`pt-${i}`}
              cx={x}
              cy={y}
              r={1.5 + intensity * 2.5}
              fill={`rgba(244, 114, 182, ${intensity})`}
            />
          );
        })}
      </svg>
    );
  }
  // Fallback — raw JSON for the operator to inspect.
  return (
    <pre
      className="text-[10px] text-slate-300 bg-slate-900 p-2 rounded max-h-[260px] overflow-auto w-full"
      data-testid={`${testIdPrefix}-raw`}
    >
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}
