import { useState } from 'react';
import { Navigate } from 'react-router-dom';
import { Eye, EyeOff, Mail, Lock, User as UserIcon, Loader2, ShieldCheck, AlertTriangle, BarChart3 } from 'lucide-react';
import { motion } from 'framer-motion';
import { useAuth } from '@/lib/auth';
import { useI18n } from '@/lib/i18n';
import { LanguageToggle } from '@/components/LanguageToggle';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { toast } from 'sonner';

export default function LoginPage() {
  const { t } = useI18n();
  const { user, login, register } = useAuth();
  const [mode, setMode] = useState('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [loading, setLoading] = useState(false);

  if (user) return <Navigate to="/" replace />;

  const useDemo = async () => {
    setEmail('demo@valuebet.app');
    setPassword('demo1234');
    setMode('login');
    setLoading(true);
    try { await login('demo@valuebet.app', 'demo1234'); } catch (e) { toast.error(e?.response?.data?.detail || t.login.errorGeneric); }
    finally { setLoading(false); }
  };

  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      if (mode === 'login') await login(email, password);
      else await register({ email, password, name: name || undefined });
      toast.success(mode === 'login' ? 'Bienvenido' : 'Cuenta creada');
    } catch (err) {
      const detail = err?.response?.data?.detail;
      toast.error(typeof detail === 'string' ? detail : t.login.errorGeneric);
    } finally { setLoading(false); }
  };

  return (
    <div className="min-h-screen w-full grid lg:grid-cols-2 bg-background text-foreground">
      {/* Left brand panel */}
      <div className="relative hidden lg:flex flex-col p-10 terminal-glow border-r border-border overflow-hidden">
        <div className="absolute inset-0 opacity-30 pointer-events-none" style={{ backgroundImage: "url('https://images.unsplash.com/photo-1556056504-5c7696c4c28d?auto=format&fit=crop&w=1200&q=60')", backgroundSize: 'cover', backgroundPosition: 'center', filter: 'blur(2px) saturate(0.7)' }} />
        <div className="absolute inset-0 bg-gradient-to-br from-background/70 via-background/85 to-background/95 pointer-events-none" />
        <div className="relative z-10 flex items-center gap-3">
          <div className="h-10 w-10 rounded-lg bg-gradient-to-br from-emerald-400 to-cyan-400 flex items-center justify-center text-background font-bold">V</div>
          <span className="text-lg font-semibold tracking-tight">{t.appName}</span>
        </div>
        <div className="relative z-10 mt-auto">
          <motion.h1 initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5 }} className="text-4xl xl:text-5xl font-semibold tracking-tight">{t.appName}</motion.h1>
          <p className="text-muted-foreground mt-3 max-w-md">{t.tagline}</p>
          <ul className="mt-8 space-y-3 text-sm">
            <li className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-emerald-300" />{t.login.bullet1}</li>
            <li className="flex items-center gap-2"><AlertTriangle className="h-4 w-4 text-amber-300" />{t.login.bullet2}</li>
            <li className="flex items-center gap-2"><BarChart3 className="h-4 w-4 text-cyan-300" />{t.login.bullet3}</li>
          </ul>
        </div>
      </div>

      {/* Right auth card */}
      <div className="flex items-center justify-center p-6 sm:p-10">
        <div className="w-full max-w-md">
          <div className="flex justify-end mb-6"><LanguageToggle /></div>
          <div className="rounded-2xl border border-border bg-card p-6 sm:p-8">
            <h2 className="text-2xl font-semibold tracking-tight">{mode === 'login' ? t.login.title : t.login.registerBtn}</h2>
            <p className="text-sm text-muted-foreground mt-1">{t.login.subtitle}</p>
            <form onSubmit={submit} className="mt-6 space-y-4">
              {mode === 'register' && (
                <div className="space-y-1.5">
                  <Label htmlFor="name">{t.login.nameLabel}</Label>
                  <div className="relative">
                    <UserIcon className="h-4 w-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                    <Input id="name" data-testid="register-name-input" className="pl-9" value={name} onChange={(e) => setName(e.target.value)} />
                  </div>
                </div>
              )}
              <div className="space-y-1.5">
                <Label htmlFor="email">{t.login.emailLabel}</Label>
                <div className="relative">
                  <Mail className="h-4 w-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                  <Input id="email" type="email" required data-testid="login-email-input" className="pl-9" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
                </div>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="password">{t.login.passwordLabel}</Label>
                <div className="relative">
                  <Lock className="h-4 w-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                  <Input id="password" type={showPw ? 'text' : 'password'} required data-testid="login-password-input" className="pl-9 pr-9" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} />
                  <button type="button" data-testid="toggle-password-btn" onClick={() => setShowPw((v) => !v)} className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-muted-foreground hover:text-foreground">
                    {showPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
              </div>
              <Button type="submit" disabled={loading} data-testid="login-submit-btn" className="w-full">
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : (mode === 'login' ? t.login.loginBtn : t.login.registerBtn)}
              </Button>
              <Button type="button" variant="secondary" onClick={useDemo} disabled={loading} data-testid="login-demo-btn" className="w-full">{t.login.useDemo}</Button>
              <p className="text-[11px] text-muted-foreground text-center mono font-mono-tabular">{t.login.demoNote}</p>
              <button type="button" data-testid="login-toggle-mode" onClick={() => setMode(mode === 'login' ? 'register' : 'login')} className="w-full text-sm text-cyan-300 hover:text-cyan-200 transition-colors">
                {mode === 'login' ? t.login.toggleToRegister : t.login.toggleToLogin}
              </button>
              <p className="text-[11px] text-muted-foreground text-center pt-2">{t.login.securityNote}</p>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
