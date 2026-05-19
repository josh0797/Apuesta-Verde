import { useEffect, useState } from 'react';
import { useI18n } from '@/lib/i18n';
import { useAuth } from '@/lib/auth';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { LogOut } from 'lucide-react';
import { SystemStatusCard } from '@/components/SystemStatusCard';

export default function ProfilePage() {
  const { t, lang } = useI18n();
  const { user, logout, refresh } = useAuth();
  const [stats, setStats] = useState(null);

  useEffect(() => {
    api.get('/stats/dashboard').then((r) => setStats(r.data)).catch(() => {});
  }, []);

  if (!user) return null;

  return (
    <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-6 md:py-8 space-y-6">
      <h1 className="text-3xl font-semibold tracking-tight">{t.profile.title}</h1>
      <div className="rounded-xl border border-border bg-card p-5 flex items-center gap-4">
        <div className="h-14 w-14 rounded-full bg-secondary flex items-center justify-center text-lg font-semibold">
          {(user.name || user.email).slice(0, 1).toUpperCase()}
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-base font-semibold truncate">{user.name || user.email}</div>
          <div className="text-xs text-muted-foreground truncate">{user.email}</div>
          <div className="text-[11px] text-muted-foreground mt-1">{t.profile.joinedOn}: {new Date(user.created_at).toLocaleDateString(lang === 'es' ? 'es-ES' : 'en-US')}</div>
        </div>
        <Button variant="secondary" onClick={logout} data-testid="profile-logout-btn"><LogOut className="h-4 w-4 mr-2" />{t.profile.signOut}</Button>
      </div>
      <div className="rounded-xl border border-border bg-card p-5">
        <div className="text-sm font-semibold uppercase tracking-wide text-muted-foreground mb-3">{t.profile.stats}</div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatBox label="Total" value={stats?.total ?? 0} />
          <StatBox label="Won" value={stats?.won ?? 0} accent="emerald" />
          <StatBox label="Lost" value={stats?.lost ?? 0} accent="red" />
          <StatBox label="Win rate" value={`${stats?.win_rate ?? 0}%`} accent="amber" />
        </div>
        <p className="text-xs text-muted-foreground mt-3 italic">{t.profile.upcomingPlaceholder}</p>
      </div>
      <SystemStatusCard />
    </div>
  );
}

function StatBox({ label, value, accent }) {
  const cls = accent === 'emerald' ? 'border-emerald-500/30 bg-emerald-500/5 text-emerald-300' : accent === 'amber' ? 'border-amber-500/30 bg-amber-500/5 text-amber-300' : accent === 'red' ? 'border-red-500/30 bg-red-500/5 text-red-300' : 'border-border bg-secondary/30';
  return (
    <div className={`rounded-lg border p-3 ${cls}`}>
      <div className="text-[11px] uppercase opacity-80">{label}</div>
      <div className="text-2xl mono font-mono-tabular font-semibold mt-0.5">{value}</div>
    </div>
  );
}
