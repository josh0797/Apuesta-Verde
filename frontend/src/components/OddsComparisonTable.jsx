import { useI18n } from '@/lib/i18n';
import { formatOdd } from '@/lib/format';

export function OddsComparisonTable({ snapshot }) {
  const { t } = useI18n();
  if (!snapshot || !snapshot.available) {
    return <div className="text-sm text-muted-foreground italic" data-testid="odds-empty">{t.match.dataIncomplete}</div>;
  }
  const rows = snapshot.markets?.['1X2'] || [];
  if (!rows.length) return <div className="text-sm text-muted-foreground italic">{t.match.dataIncomplete}</div>;
  // Find best per column
  const best = { home: 0, draw: 0, away: 0 };
  rows.forEach((r) => {
    if ((r.home || 0) > best.home) best.home = r.home;
    if ((r.draw || 0) > best.draw) best.draw = r.draw;
    if ((r.away || 0) > best.away) best.away = r.away;
  });
  return (
    <div className="overflow-x-auto rounded-lg border border-border" data-testid="odds-comparison-table">
      <table className="w-full text-sm">
        <thead className="bg-secondary/50">
          <tr>
            <th className="text-left px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground font-medium">{t.match.bookmaker}</th>
            <th className="text-right px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground font-medium">{t.match.home}</th>
            <th className="text-right px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground font-medium">{t.match.draw}</th>
            <th className="text-right px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground font-medium">{t.match.away}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-border hover:bg-white/[0.03] transition-colors">
              <td className="px-3 py-2 text-foreground/90">{r.bookmaker}</td>
              <td className={`px-3 py-2 text-right mono font-mono-tabular ${r.home === best.home && best.home ? 'text-emerald-300 font-semibold' : ''}`}>{formatOdd(r.home)}</td>
              <td className={`px-3 py-2 text-right mono font-mono-tabular ${r.draw === best.draw && best.draw ? 'text-emerald-300 font-semibold' : ''}`}>{formatOdd(r.draw)}</td>
              <td className={`px-3 py-2 text-right mono font-mono-tabular ${r.away === best.away && best.away ? 'text-emerald-300 font-semibold' : ''}`}>{formatOdd(r.away)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
