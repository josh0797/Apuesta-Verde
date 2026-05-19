import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, ReferenceLine } from 'recharts';
import { useI18n } from '@/lib/i18n';

export function WinRateChart({ data }) {
  const { t } = useI18n();
  if (!data || data.length === 0) {
    return (
      <div className="text-sm text-muted-foreground italic px-4 py-8 text-center" data-testid="winrate-chart-empty">
        {t.history.empty}
      </div>
    );
  }
  const chartData = data.map((d, i) => ({
    idx: i + 1,
    rate: d.win_rate,
    label: d.match_label,
    outcome: d.outcome,
  }));
  return (
    <div className="h-56 w-full" data-testid="winrate-chart">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={chartData} margin={{ top: 5, right: 10, left: -10, bottom: 0 }}>
          <CartesianGrid stroke="#1d2531" strokeDasharray="3 3" />
          <XAxis dataKey="idx" stroke="#6b7280" fontSize={11} tickLine={false} axisLine={false} />
          <YAxis stroke="#6b7280" fontSize={11} tickLine={false} axisLine={false} domain={[0, 100]} unit="%" />
          <Tooltip
            contentStyle={{ background: '#0f1422', border: '1px solid #243046', borderRadius: 8, fontSize: 12 }}
            labelStyle={{ color: '#9aa4b2' }}
            formatter={(v, n) => [`${v}%`, t.history.winRate]}
            labelFormatter={(l) => `${t.history.settled} #${l}`}
          />
          <ReferenceLine y={50} stroke="#374151" strokeDasharray="4 4" />
          <Line type="monotone" dataKey="rate" stroke="#2EE59D" strokeWidth={2.2} dot={{ r: 3, fill: '#2EE59D' }} activeDot={{ r: 5 }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
