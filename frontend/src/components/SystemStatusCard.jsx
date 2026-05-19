import { useEffect, useState } from 'react';
import { Activity, CheckCircle2, XCircle } from 'lucide-react';
import { useI18n } from '@/lib/i18n';
import { api } from '@/lib/api';

export function SystemStatusCard() {
  const { t, lang } = useI18n();
  const [status, setStatus] = useState(null);
  useEffect(() => {
    api.get('/system/status').then((r) => setStatus(r.data)).catch(() => {});
  }, []);
  if (!status) return null;
  const sch = status.scheduler || {};
  const prov = status.providers || {};
  const fmtDate = (iso) => {
    if (!iso) return '—';
    try { return new Date(iso).toLocaleString(lang === 'es' ? 'es-ES' : 'en-US', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }); } catch { return iso; }
  };
  return (
    <div className="rounded-xl border border-border bg-card p-5" data-testid="system-status-card">
      <div className="text-sm font-semibold uppercase tracking-wide text-muted-foreground mb-3">{t.profile.system}</div>
      <div className="grid sm:grid-cols-2 gap-4">
        <div>
          <div className="text-xs text-muted-foreground mb-2 flex items-center gap-1.5"><Activity className="h-3.5 w-3.5" />{t.profile.scheduler}</div>
          <div className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs border ${sch.enabled ? 'bg-emerald-500/10 text-emerald-200 border-emerald-500/30' : 'bg-slate-500/10 text-slate-200 border-slate-500/30'}`}>
            {sch.enabled ? <CheckCircle2 className="h-3 w-3" /> : <XCircle className="h-3 w-3" />}
            {sch.enabled ? t.profile.schedulerEnabled : t.profile.schedulerDisabled}
          </div>
          {sch.enabled && sch.jobs && (
            <div className="mt-2 space-y-1 text-[11px] text-muted-foreground">
              {Object.entries(sch.jobs).map(([id, j]) => (
                <div key={id} className="flex items-center justify-between border-b border-border/40 pb-1">
                  <span className="mono font-mono-tabular">{id}</span>
                  <span className="mono font-mono-tabular">{t.profile.nextRun}: {fmtDate(j.next_run)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div>
          <div className="text-xs text-muted-foreground mb-2">{t.profile.providers}</div>
          <div className="flex flex-wrap gap-1.5">
            <ProvBadge label="OpenAI gpt-4o-mini" ok={prov.openai_configured} />
            <ProvBadge label="Emergent" ok={prov.emergent_configured} />
            <ProvBadge label="API-Football" ok={prov.api_football_configured} />
          </div>
        </div>
      </div>
    </div>
  );
}

function ProvBadge({ label, ok }) {
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] border ${ok ? 'bg-emerald-500/10 text-emerald-200 border-emerald-500/30' : 'bg-red-500/10 text-red-300 border-red-500/30'}`}>
      {ok ? <CheckCircle2 className="h-3 w-3" /> : <XCircle className="h-3 w-3" />}
      {label}
    </span>
  );
}
