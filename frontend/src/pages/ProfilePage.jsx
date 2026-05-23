import { useEffect, useState } from 'react';
import { useI18n } from '@/lib/i18n';
import { useAuth } from '@/lib/auth';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { LogOut } from 'lucide-react';
import { SystemStatusCard } from '@/components/SystemStatusCard';
import { SportStatsPanel } from '@/components/SportStatsPanel';

export default function ProfilePage() {
  const { t, lang } = useI18n();
  const { user, logout, refresh } = useAuth();
  const [stats, setStats] = useState(null);

  useEffect(() => {
    api.get('/stats/dashboard').then((r) => setStats(r.data)).catch(() => {});
  }, []);

  if (!user) return null;

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-6 md:py-8 space-y-6">
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

      {/* Phase P2 — Sport-segmented stats (replaces the legacy 4-tile block) */}
      <div className="rounded-xl border border-border bg-card p-5 space-y-4">
        <div className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">{t.profile.stats}</div>
        <SportStatsPanel data={stats} lang={lang} stake={10} testId="profile-sport-stats" />
      </div>

      <SystemStatusCard />
    </div>
  );
}
