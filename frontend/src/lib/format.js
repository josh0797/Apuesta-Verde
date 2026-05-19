export function formatOdd(v) {
  if (v === null || v === undefined || isNaN(v)) return '—';
  return Number(v).toFixed(2);
}

export function formatDateTime(iso, lang = 'es') {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    const locale = lang === 'es' ? 'es-ES' : 'en-US';
    return d.toLocaleString(locale, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
  } catch { return iso; }
}

export function relativeTime(iso, lang = 'es') {
  if (!iso) return '—';
  const d = new Date(iso);
  const diffMs = d.getTime() - Date.now();
  const absMin = Math.round(Math.abs(diffMs) / 60000);
  const isFuture = diffMs > 0;
  const unit = absMin < 60 ? 'min' : absMin < 1440 ? 'h' : 'd';
  const v = unit === 'min' ? absMin : unit === 'h' ? Math.round(absMin / 60) : Math.round(absMin / 1440);
  if (lang === 'en') return isFuture ? `in ${v}${unit}` : `${v}${unit} ago`;
  return isFuture ? `en ${v}${unit}` : `hace ${v}${unit}`;
}

export function confidenceTier(score) {
  if (score >= 88) return 'Maxima';
  if (score >= 78) return 'Alta';
  if (score >= 68) return 'Media';
  return 'Below';
}

export function tierClass(tier) {
  if (tier === 'Maxima') return 'bg-amber-500/15 text-amber-200 border border-amber-500/30';
  if (tier === 'Alta') return 'bg-emerald-500/15 text-emerald-200 border border-emerald-500/30';
  if (tier === 'Media') return 'bg-cyan-500/15 text-cyan-200 border border-cyan-500/30';
  return 'bg-slate-500/15 text-slate-200 border border-slate-500/30';
}
