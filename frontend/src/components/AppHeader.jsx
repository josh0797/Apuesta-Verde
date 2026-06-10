import { Link, useLocation, useNavigate } from 'react-router-dom';
import { LayoutDashboard, Activity, History, UserRound, LogOut, ChevronDown, Target } from 'lucide-react';
import { useI18n } from '@/lib/i18n';
import { useAuth } from '@/lib/auth';
import { useSport, sportLabel } from '@/lib/sport';
import { LanguageToggle } from './LanguageToggle';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

function SportSwitcher() {
  const { lang } = useI18n();
  const { sport, setSport, sports } = useSport();
  const current = sports.find((s) => s.id === sport) || sports[0];
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          data-testid="sport-switcher-trigger"
          className="inline-flex items-center gap-1.5 pl-2 pr-1.5 py-1 rounded-md border border-border bg-card/60 hover:border-cyan-500/30 transition-colors text-xs"
        >
          <span className="text-base leading-none" aria-hidden>{current?.icon}</span>
          <span className="font-medium hidden sm:inline">{sportLabel(current, lang)}</span>
          <ChevronDown className="h-3 w-3 opacity-70" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-[180px]">
        <DropdownMenuLabel className="text-[10px] uppercase tracking-wide opacity-70">
          {lang === 'es' ? 'Deporte' : 'Sport'}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {sports.map((s) => (
          <DropdownMenuItem
            key={s.id}
            onClick={() => setSport(s.id)}
            data-testid={`sport-option-${s.id}`}
            className={`flex items-center gap-2 ${s.id === sport ? 'text-emerald-300 bg-emerald-500/5' : ''}`}
          >
            <span className="text-base leading-none" aria-hidden>{s.icon}</span>
            <span className="flex-1">{sportLabel(s, lang)}</span>
            {s.id === sport && <span className="text-[10px] opacity-70">●</span>}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function AppHeader() {
  const { t } = useI18n();
  const { user, logout } = useAuth();
  const { pathname } = useLocation();
  const navigate = useNavigate();

  const tabs = [
    { to: '/', icon: LayoutDashboard, label: t.nav.dashboard, test: 'nav-dashboard' },
    { to: '/live', icon: Activity, label: t.nav.live, test: 'nav-live' },
    { to: '/history', icon: History, label: t.nav.history, test: 'nav-history' },
    { to: '/dashboard/calibration', icon: Target,
      label: t.nav.calibration || 'Calibración', test: 'nav-calibration' },
    { to: '/profile', icon: UserRound, label: t.nav.profile, test: 'nav-profile' },
  ];

  return (
    <header className="sticky top-0 z-40 bg-background/70 backdrop-blur supports-[backdrop-filter]:bg-background/50 border-b border-border">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-14 flex items-center gap-3">
        <Link to="/" className="flex items-center gap-2 shrink-0" data-testid="app-logo">
          <div className="h-7 w-7 rounded-md bg-gradient-to-br from-emerald-400 to-cyan-400 flex items-center justify-center text-background font-bold text-sm">V</div>
          <span className="hidden lg:inline text-sm font-semibold tracking-tight">Value Bet Intelligence</span>
        </Link>
        <SportSwitcher />
        <nav className="flex items-center gap-1 ml-2 overflow-x-auto">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const active = (tab.to === '/' && pathname === '/') || (tab.to !== '/' && pathname.startsWith(tab.to));
            return (
              <Link key={tab.to} to={tab.to} data-testid={tab.test} className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${active ? 'bg-secondary text-foreground' : 'text-muted-foreground hover:text-foreground hover:bg-white/5'}`}>
                <Icon className="h-4 w-4" />
                <span className="hidden md:inline">{tab.label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <LanguageToggle />
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button data-testid="user-menu-trigger" className="inline-flex items-center gap-2 px-2 py-1 rounded-md border border-border bg-card/60 hover:border-cyan-500/30 transition-colors">
                <div className="h-6 w-6 rounded-full bg-secondary flex items-center justify-center text-[11px] font-semibold">
                  {(user?.name || user?.email || 'U').slice(0, 1).toUpperCase()}
                </div>
                <span className="hidden sm:inline text-xs text-muted-foreground">{user?.email}</span>
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuLabel>{user?.email}</DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => navigate('/profile')} data-testid="menu-profile">{t.nav.profile}</DropdownMenuItem>
              <DropdownMenuItem onClick={logout} data-testid="menu-logout" className="text-red-300 focus:text-red-200">
                <LogOut className="h-3.5 w-3.5 mr-2" />{t.profile.signOut}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    </header>
  );
}
