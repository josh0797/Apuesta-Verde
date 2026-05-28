import { useState } from 'react';
import { Newspaper, AlertTriangle, ChevronDown, Activity, Eye } from 'lucide-react';

/**
 * EditorialContextPanel — Renders the P3 Editorial Context block.
 *
 * Inputs (all optional — panel renders nothing when none are present):
 *   editorial          — the editorial_context consensus dict (see backend
 *                        services/editorial_context/editorial_normalizer.py)
 *   interpretation     — the moneyball_interpretation.interpret() output
 *                        (alignment + flags + narrative + modifier)
 *   testId             — data-testid prefix for QA hooks
 *
 * The panel is COLLAPSED by default and uses a calm slate/blue palette so it
 * doesn't fight for attention with the Moneyball / Picks visuals.
 */
export function EditorialContextPanel({ editorial, interpretation, testId = 'editorial-context-panel' }) {
  const [open, setOpen] = useState(false);
  if (!editorial || !editorial.available) return null;

  const alignment = interpretation?.alignment || 'NO_EDITORIAL';
  const flags = interpretation?.flags || [];
  const modifier = interpretation?.confidence_modifier ?? 0;
  const hasPublicRisk = flags.includes('PUBLIC_NARRATIVE_RISK');
  const hasBias = flags.includes('HIGH_NARRATIVE_BIAS') || (editorial.narrative_bias_score || 0) >= 70;

  // Pick a tone based on the dominant flag.
  const tone = hasPublicRisk
    ? { border: 'border-amber-500/30', bg: 'bg-amber-500/5', icon: 'text-amber-300', accent: 'text-amber-200' }
    : alignment === 'AGREES'
      ? { border: 'border-cyan-500/30', bg: 'bg-cyan-500/5', icon: 'text-cyan-300', accent: 'text-cyan-200' }
      : { border: 'border-slate-500/30', bg: 'bg-slate-500/5', icon: 'text-slate-300', accent: 'text-slate-200' };

  const sources = editorial.sources || [];
  const cm = editorial.consensus_market;
  const motivationNotes = editorial.motivation_notes || [];
  const factualNotes = editorial.factual_notes || [];
  const injuryNotes = editorial.injury_notes || [];
  const risks = editorial.risks || [];

  return (
    <div
      className={`mt-3 rounded-lg border ${tone.border} ${tone.bg} text-xs`}
      data-testid={testId}
    >
      {/* Header */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-2 px-3 py-2"
        data-testid={`${testId}-toggle`}
        aria-expanded={open}
      >
        <div className="flex items-center gap-2 min-w-0">
          <Newspaper className={`h-3.5 w-3.5 ${tone.icon} shrink-0`} />
          <span className={`text-[11px] uppercase tracking-wide font-semibold ${tone.accent}`}>
            Contexto editorial
          </span>
          {sources.length > 0 && (
            <span className="text-[10px] text-muted-foreground">
              · {sources.length} {sources.length === 1 ? 'fuente' : 'fuentes'}
            </span>
          )}
          {cm && (
            <span className="text-[10px] font-mono text-foreground/80 truncate max-w-[220px]" data-testid={`${testId}-consensus-market`}>
              · sugiere {cm}
            </span>
          )}
          {hasPublicRisk && (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-amber-500/20 text-amber-200 border border-amber-500/30">
              <AlertTriangle className="h-3 w-3" />
              public narrative risk
            </span>
          )}
          {hasBias && (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-rose-500/20 text-rose-200 border border-rose-500/30">
              hype detectado
            </span>
          )}
        </div>
        <ChevronDown className={`h-3.5 w-3.5 text-muted-foreground transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="px-3 pb-3 pt-1 space-y-3 border-t border-border/30">
          {/* Editorial summary */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <Metric label="Frescura" value={`${editorial.freshness_score ?? 0}/100`} accent="cyan" />
            <Metric label="Fiabilidad" value={`${editorial.reliability_score ?? 0}/100`} accent="emerald" />
            <Metric label="Sesgo narrativo" value={`${editorial.narrative_bias_score ?? 0}/100`} accent={editorial.narrative_bias_score >= 70 ? 'rose' : 'slate'} />
            <Metric label="Alineación motor" value={alignment} accent={alignment === 'AGREES' ? 'emerald' : alignment === 'DISAGREES' ? 'rose' : 'slate'} />
          </div>

          {/* Engine reading (Moneyball interpretation) */}
          {interpretation?.narrative && (
            <div className="rounded-md bg-background/40 border border-border/40 px-3 py-2">
              <div className="flex items-center gap-2 mb-1">
                <Activity className="h-3 w-3 text-foreground/70" />
                <span className="text-[10px] uppercase tracking-wide text-foreground/70 font-semibold">
                  Lectura del motor
                </span>
                {modifier !== 0 && (
                  <span className={`text-[10px] font-mono ${modifier > 0 ? 'text-emerald-300' : 'text-amber-300'}`}>
                    {modifier > 0 ? '+' : ''}{modifier} confianza
                  </span>
                )}
              </div>
              <p className="text-muted-foreground leading-relaxed">{interpretation.narrative}</p>
            </div>
          )}

          {/* Argumentos editoriales (factual + motivación) */}
          {(factualNotes.length > 0 || motivationNotes.length > 0) && (
            <div>
              <div className="text-[10px] uppercase tracking-wide text-foreground/70 font-semibold mb-1">
                Argumentos de la redacción
              </div>
              <ul className="space-y-1 text-muted-foreground">
                {motivationNotes.slice(0, 4).map((n, i) => (
                  <li key={`m-${i}`} className="pl-3 border-l border-cyan-500/30">{n}</li>
                ))}
                {factualNotes.slice(0, 4).map((n, i) => (
                  <li key={`f-${i}`} className="pl-3 border-l border-emerald-500/30">{n}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Bajas */}
          {injuryNotes.length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-wide text-rose-200 font-semibold mb-1">
                Bajas mencionadas
              </div>
              <ul className="space-y-1 text-muted-foreground">
                {injuryNotes.slice(0, 3).map((n, i) => (
                  <li key={`i-${i}`} className="pl-3 border-l border-rose-500/30">{n}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Riesgos */}
          {risks.length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-wide text-amber-200 font-semibold mb-1">
                Riesgos mencionados
              </div>
              <ul className="space-y-1 text-muted-foreground">
                {risks.slice(0, 3).map((n, i) => (
                  <li key={`r-${i}`} className="pl-3 border-l border-amber-500/30">{n}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Fuentes */}
          {sources.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 pt-1 border-t border-border/30">
              <Eye className="h-3 w-3 text-muted-foreground" />
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">Fuentes</span>
              {sources.map((s) => (
                <span key={s} className="text-[10px] px-1.5 py-0.5 rounded-md bg-foreground/5 border border-border/40 text-foreground/70">
                  {s}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Metric({ label, value, accent = 'slate' }) {
  const accentMap = {
    cyan: 'text-cyan-200 border-cyan-500/30',
    emerald: 'text-emerald-200 border-emerald-500/30',
    rose: 'text-rose-200 border-rose-500/30',
    amber: 'text-amber-200 border-amber-500/30',
    slate: 'text-slate-200 border-slate-500/30',
  };
  const cls = accentMap[accent] || accentMap.slate;
  return (
    <div className={`rounded-md border ${cls.split(' ')[1]} bg-background/40 px-2 py-1.5`}>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className={`text-xs font-mono ${cls.split(' ')[0]}`}>{value}</div>
    </div>
  );
}

export default EditorialContextPanel;
