import { Link, useLocation, useNavigate } from 'react-router-dom';
import { LayoutDashboard, Activity, History, UserRound, LogOut } from 'lucide-react';
import { useI18n } from '@/lib/i18n';
import { useAuth } from '@/lib/auth';
import { LanguageToggle } from './LanguageToggle';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

export function AppHeader() {
  const { t } = useI18n();
  const { user, logout } = useAuth();
  const { pathname } = useLocation();
  const navigate = useNavigate();

  const tabs = [
    { to: '/', icon: LayoutDashboard, label: t.nav.dashboard, test: 'nav-dashboard' },
    { to: '/live', icon: Activity, label: t.nav.live, test: 'nav-live' },
    { to: '/history', icon: History, label: t.nav.history, test: 'nav-history' },
    { to: '/profile', icon: UserRound, label: t.nav.profile, test: 'nav-profile' },
  ];

  return (
    <header className="sticky top-0 z-40 bg-background/70 backdrop-blur supports-[backdrop-filter]:bg-background/50 border-b border-border">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-14 flex items-center gap-4">
        <Link to="/" className="flex items-center gap-2 shrink-0" data-testid="app-logo">
          <div className="h-7 w-7 rounded-md bg-gradient-to-br from-emerald-400 to-cyan-400 flex items-center justify-center text-background font-bold text-sm">V</div>
          <span className="hidden sm:inline text-sm font-semibold tracking-tight">Value Bet Intelligence</span>
        </Link>
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
