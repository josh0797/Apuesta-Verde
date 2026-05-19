import { createContext, useContext, useEffect, useMemo, useState, useCallback } from 'react';
import { api } from './api';

/**
 * SportContext — controls which sport (football | basketball | baseball) is currently
 * selected across the app. Persists to localStorage so the choice survives reloads.
 *
 * Pages read the current `sport` from this context and include it in every relevant
 * API call. The selector lives in the AppHeader.
 */

const STORAGE_KEY = 'vbi_sport';
const DEFAULT_SPORT = 'football';

const FALLBACK_SPORTS = [
  { id: 'football', label: 'Fútbol', label_en: 'Football', icon: '⚽' },
  { id: 'basketball', label: 'NBA / Basket', label_en: 'NBA / Basketball', icon: '🏀' },
  { id: 'baseball', label: 'MLB / Béisbol', label_en: 'MLB / Baseball', icon: '⚾' },
];

const SportContext = createContext({
  sport: DEFAULT_SPORT,
  setSport: () => {},
  sports: FALLBACK_SPORTS,
  loading: false,
});

export function SportProvider({ children }) {
  const [sport, setSportState] = useState(() => localStorage.getItem(STORAGE_KEY) || DEFAULT_SPORT);
  const [sports, setSports] = useState(FALLBACK_SPORTS);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await api.get('/meta/sports');
        if (!cancelled && Array.isArray(r.data?.sports) && r.data.sports.length > 0) {
          setSports(r.data.sports);
        }
      } catch (_) {
        // keep fallback list silently
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const setSport = useCallback((next) => {
    const allowed = (sports.length ? sports : FALLBACK_SPORTS).map((s) => s.id);
    const safe = allowed.includes(next) ? next : DEFAULT_SPORT;
    setSportState(safe);
    try { localStorage.setItem(STORAGE_KEY, safe); } catch (_) {}
  }, [sports]);

  const value = useMemo(() => ({ sport, setSport, sports, loading }), [sport, setSport, sports, loading]);
  return <SportContext.Provider value={value}>{children}</SportContext.Provider>;
}

export function useSport() {
  return useContext(SportContext);
}

/** Returns label for the active sport in the given language ('es' | 'en'). */
export function sportLabel(sportObj, lang) {
  if (!sportObj) return '';
  if (lang === 'en') return sportObj.label_en || sportObj.label;
  return sportObj.label;
}
