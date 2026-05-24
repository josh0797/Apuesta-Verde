/**
 * lib/liveValidation.js — client-side mirror of `services/live_lifecycle.py`.
 *
 * Used as a defence-in-depth layer: even when the backend has marked a
 * match valid, if its heartbeat ages past the per-sport TTL while the
 * page sits open we hide it locally — so the user never sees a "90'
 * stale" card just because they left the tab open.
 *
 * Source of truth for status codes lives in the backend. These tables
 * must stay in sync with `LIVE_STATUSES` / `FINISHED_STATUSES` /
 * `HEARTBEAT_STALE_SEC` over there.
 */

export const LIVE_STATUSES = {
  football: new Set(['1H', 'HT', '2H', 'ET', 'BT', 'P', 'LIVE']),
  basketball: new Set(['Q1', 'Q2', 'Q3', 'Q4', 'OT', 'BT', 'HT', 'LIVE', 'IN_PLAY', 'in_play']),
  baseball: new Set([
    'IN1', 'IN2', 'IN3', 'IN4', 'IN5', 'IN6', 'IN7', 'IN8', 'IN9',
    'BT', 'MID', 'END', 'LIVE', 'in_play', 'IN_PLAY',
  ]),
};

export const FINISHED_STATUSES = {
  football: new Set(['FT', 'AET', 'PEN', 'PST', 'CANC', 'ABD', 'AWD', 'WO', 'NS', 'TBD', 'SUSP', 'INT']),
  basketball: new Set(['FT', 'FINAL', 'Final', 'ENDED', 'POST', 'CANC', 'AOT', 'AWD']),
  baseball: new Set(['FT', 'FINAL', 'Final', 'ENDED', 'POST', 'CANC', 'AWD', 'SUSP', 'Completed']),
};

export const LIVE_CACHE_TTL_SEC = {
  football: 60,
  basketball: 30,
  baseball: 45,
};

export const HEARTBEAT_STALE_SEC = {
  football: 10 * 60,
  basketball: 5 * 60,
  baseball: 10 * 60,
};

export const FOOTBALL_HARD_MINUTE_CAP = 105;

function parseIso(ts) {
  if (!ts || typeof ts !== 'string') return null;
  const d = Date.parse(ts);
  return Number.isNaN(d) ? null : d;
}

export function heartbeatAgeSec(match) {
  const ts = parseIso(match?.updated_at);
  if (ts == null) return null;
  return Math.max(0, Math.floor((Date.now() - ts) / 1000));
}

export function liveMinute(match) {
  const m = match?.live_stats?.minute;
  return Number.isFinite(m) ? Math.floor(m) : null;
}

export function statusShort(match) {
  return match?.status_short || match?.live_stats?.status || null;
}

/** Mirror of `is_match_live()` in backend live_lifecycle.py */
export function validLiveMatch(match, { now = Date.now() } = {}) {
  if (!match || typeof match !== 'object') return false;
  const sport = (match.sport || 'football').toLowerCase();
  const status = statusShort(match);
  if (!status) return false;
  if ((FINISHED_STATUSES[sport] || new Set()).has(status)) return false;
  if (!(LIVE_STATUSES[sport] || new Set()).has(status)) return false;

  const age = heartbeatAgeSec(match);
  if (age == null) return false;
  if (age > (HEARTBEAT_STALE_SEC[sport] || 600)) return false;

  if (sport === 'football') {
    const minute = liveMinute(match);
    if (minute != null && minute >= FOOTBALL_HARD_MINUTE_CAP) return false;
    if (status === '2H' && minute != null && minute >= 95) return false;
  }

  if (match.is_live === false) return false;
  return true;
}

export function isLiveExpired(match, { now = Date.now() } = {}) {
  if (!match) return true;
  if (!match.is_live) return true;
  return !validLiveMatch(match, { now });
}

/** Validate a list, returning { valid, archived } so the caller can log
 *  how many we filtered. */
export function partitionLive(items = [], { now = Date.now() } = {}) {
  const valid = [];
  const archived = [];
  for (const m of items) {
    if (validLiveMatch(m, { now })) {
      valid.push(m);
    } else {
      archived.push(m);
      // eslint-disable-next-line no-console
      console.log('[LIVE_MATCH_EXPIRED]', {
        match_id: m?.match_id,
        status: statusShort(m),
        minute: liveMinute(m),
        age_sec: heartbeatAgeSec(m),
      });
    }
  }
  return { valid, archived };
}
