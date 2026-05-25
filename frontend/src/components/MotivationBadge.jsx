import { useState } from 'react';
import {
  Activity, Trophy, ShieldOff, ShieldHalf, Star, Crown,
  HeartCrack, Flame, Clock, Plus, Minus, ChevronDown, TrendingUp,
} from 'lucide-react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { MOTIVATION_STATES, PRESSURE_STATES, resolveMotivationState } from '@/lib/intelligence';

const STATE_ICON_MAP = { Flame, TrendingUp, Clock, Activity, Trophy };

/** Compact badge — kept for legacy callers (MatchCard, MatchDetail) */
const LEVELS = {
  1: { label_es: 'Mínima', label_en: 'Min', cls: 'bg-red-500/15 text-red-300 border-red-500/30', tone: 'rose' },
  2: { label_es: 'Baja', label_en: 'Low', cls: 'bg-orange-500/15 text-orange-300 border-orange-500/30', tone: 'amber' },
  3: { label_es: 'Media', label_en: 'Med', cls: 'bg-yellow-500/15 text-yellow-200 border-yellow-500/30', tone: 'amber' },
  4: { label_es: 'Alta', label_en: 'High', cls: 'bg-emerald-500/15 text-emerald-200 border-emerald-500/30', tone: 'emerald' },
  5: { label_es: 'Máxima', label_en: 'Max', cls: 'bg-cyan-500/15 text-cyan-200 border-cyan-500/30', tone: 'cyan' },
};

export function MotivationBadge({ level, lang = 'es', tooltip }) {
  const l = LEVELS[level] || LEVELS[3];
  const label = lang === 'es' ? l.label_es : l.label_en;
  const node = (
    <span
      data-testid={`motivation-badge-${level}`}
      className={`inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-md border ${l.cls}`}
    >
      <Activity className="h-3 w-3" />
      <span className="font-mono-tabular">{level}</span>
      <span>{label}</span>
    </span>
  );
  if (!tooltip) return node;
  return (
    <TooltipProvider delayDuration={120}>
      <Tooltip>
        <TooltipTrigger asChild>{node}</TooltipTrigger>
        <TooltipContent className="glass-surface text-xs max-w-[260px] leading-relaxed">{tooltip}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/* ──────────────────────────────────────────────────────────────────────
   MotivationContextBlock
   - Explains motivation with reasons, sources, urgency, gameplay impact
   - Heuristically derives the narrative from level + provided reason text
   - Pure UI (no state shared); content is read from `motivation` prop
   ────────────────────────────────────────────────────────────────────── */

const REASON_DETECTORS = [
  { key: 'title-race',          patterns: [/titul/i, /liguilla/i, /campeon/i, /pelea por el t[ií]tulo/i],          icon: Trophy,    tone: 'cyan',    label_es: 'Pelea por el título',  label_en: 'Title race' },
  { key: 'relegation',          patterns: [/descenso/i, /relegation/i, /salvación/i, /salvacion/i],                 icon: ShieldOff, tone: 'rose',    label_es: 'Pelea por descenso',   label_en: 'Relegation battle' },
  { key: 'european-spot',       patterns: [/europa/i, /champions/i, /europe/i, /europeo/i],                          icon: Star,      tone: 'cyan',    label_es: 'Plaza europea',        label_en: 'European qualification' },
  { key: 'derby',               patterns: [/derbi/i, /derby/i, /clásic/i, /clasic/i, /rival/i],                       icon: Flame,     tone: 'amber',   label_es: 'Derbi / rivalidad',     label_en: 'Derby / rivalry' },
  { key: 'revenge',             patterns: [/revancha/i, /revenge/i, /venganza/i],                                    icon: HeartCrack, tone: 'rose',   label_es: 'Partido de revancha',  label_en: 'Revenge match' },
  { key: 'crowned',             patterns: [/campeón/i, /already crowned/i, /asegurad/i, /ya campe/i],                 icon: Crown,     tone: 'slate',   label_es: 'Campeón / objetivo asegurado', label_en: 'Already crowned' },
  { key: 'eliminated',          patterns: [/eliminado/i, /eliminated/i, /irrelevante/i, /nada en juego/i, /no urgency/i, /sin urgenc/i], icon: Clock, tone: 'slate', label_es: 'Sin urgencia competitiva', label_en: 'No competitive urgency' },
  { key: 'high-pressure',       patterns: [/presión/i, /must.win/i, /obligado/i, /key match/i, /partido clave/i],                                                       icon: ShieldHalf,  tone: 'amber', label_es: 'Partido bisagra',             label_en: 'High-pressure match' },
  { key: 'rotation',            patterns: [/rotaci[oó]n/i, /descanso/i, /rotation/i, /resting/i, /suplentes/i, /reserve/i, /titulares descansan/i],                     icon: Clock,       tone: 'slate', label_es: 'Rotación probable',            label_en: 'Rotation likely' },
  { key: 'nothing-to-play',     patterns: [/nada (que|en) jugar/i, /sin nada/i, /nothing to play/i, /matem[aá]ticamente/i, /ya (clasificado|descendido|campe[oó]n)/i],  icon: Clock,       tone: 'slate', label_es: 'Sin nada en juego',            label_en: 'Nothing to play for' },
  { key: 'qualification',       patterns: [/clasific/i, /qualification/i, /clasificaci[oó]n/i, /acceder/i, /pase a/i, /boleto/i],                                       icon: Star,        tone: 'cyan',  label_es: 'Pelea por clasificación',      label_en: 'Qualification battle' },
  { key: 'playoff-push',        patterns: [/playoff/i, /liguilla/i, /postemporada/i, /wildcard/i, /repechaje/i],                                                        icon: Trophy,      tone: 'cyan',  label_es: 'Pelea por playoff',            label_en: 'Playoff push' },
];

function detectReasons(text) {
  if (!text || typeof text !== 'string') return [];
  const matches = [];
  for (const det of REASON_DETECTORS) {
    if (det.patterns.some((rx) => rx.test(text))) matches.push(det);
    if (matches.length >= 3) break;
  }
  return matches;
}

/** Heuristic gameplay impact derived from motivation level on one side */
function impactForLevel(level) {
  if (level >= 5) {
    return {
      positive_es: ['+ Intensidad atacante máxima', '+ Pressing alto sostenido'],
      positive_en: ['+ Max attacking intensity', '+ Sustained high pressing'],
      negative_es: ['− Mayor riesgo de tarjetas', '− Posible falta de control emocional'],
      negative_en: ['− Card risk increases', '− Possible emotional volatility'],
    };
  }
  if (level >= 4) {
    return {
      positive_es: ['+ Intensidad atacante alta', '+ Concentración táctica'],
      positive_en: ['+ High attacking intensity', '+ Tactical focus'],
      negative_es: ['− Posibles bajas por sanción'],
      negative_en: ['− Possible suspensions'],
    };
  }
  if (level === 3) {
    return {
      positive_es: ['+ Plantilla regular esperada'],
      positive_en: ['+ Regular lineup expected'],
      negative_es: ['− Rendimiento medio sin sorpresas'],
      negative_en: ['− Average output, no surprises'],
    };
  }
  if (level === 2) {
    return {
      positive_es: ['+ Posible debut de canteranos'],
      positive_en: ['+ Possible youth debuts'],
      negative_es: ['− Estructura defensiva más débil', '− Rotación probable'],
      negative_en: ['− Weaker defensive structure', '− Rotation likely'],
    };
  }
  return {
    positive_es: [],
    positive_en: [],
    negative_es: ['− Sin motivación competitiva', '− Rotación masiva probable', '− Resultado impredecible'],
    negative_en: ['− No competitive motivation', '− Heavy rotation likely', '− Unpredictable outcome'],
  };
}

/**
 * Props
 *   - motivation: { home: { level, label, reason }, away: { level, label, reason } }
 *   - motivationState: optional override 'HIGH_BOTH' | 'ASYMMETRIC_HIGH_LOW' | 'LOW_BOTH' | 'NORMAL'
 *   - homeName / awayName: optional team display names
 *   - lang: 'es' | 'en'
 */
export function MotivationContextBlock({ motivation, motivationState, pressureState, homeName, awayName, lang = 'es' }) {
  const [open, setOpen] = useState(true);
  if (!motivation) return null;
  const home = motivation.home || {};
  const away = motivation.away || {};
  const homeReasons = detectReasons(home.reason);
  const awayReasons = detectReasons(away.reason);
  const homeImpact = impactForLevel(home.level || 3);
  const awayImpact = impactForLevel(away.level || 3);

  // Resolve the motivation_state for the whole match (LLM-provided > derived)
  const resolvedState = (
    motivationState
    || resolveMotivationState({ motivation_state: undefined, motivation })
    || 'NORMAL'
  );
  const stateMeta = MOTIVATION_STATES[resolvedState] || MOTIVATION_STATES.NORMAL;
  const StateIcon = STATE_ICON_MAP[stateMeta.icon] || Activity;

  // Pressure state (competition-stage layer) — optional; shown only when
  // the backend provided it and it's not the "NORMAL_LEAGUE" default.
  const pressureMeta = pressureState && PRESSURE_STATES[pressureState];
  const showPressure = pressureMeta && pressureState !== 'NORMAL_LEAGUE';
  const PressureIcon = showPressure ? (STATE_ICON_MAP[pressureMeta.icon] || Trophy) : null;

  const t = lang === 'en'
    ? { header: 'Motivation context', reasons: 'REASONS', sources: 'SOURCES', impact: 'EXPECTED GAMEPLAY IMPACT', unknown: 'No specific reason detected' }
    : { header: 'Contexto motivacional', reasons: 'RAZONES', sources: 'FUENTES', impact: 'IMPACTO ESPERADO EN JUEGO', unknown: 'Sin razón específica detectada' };

  return (
    <div data-testid="motivation-context-block" data-motivation-state={resolvedState} className="rounded-xl border border-border/80 bg-card/60 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-3 px-4 py-3 border-b border-border/50 hover:bg-white/[0.02] transition-colors"
        aria-expanded={open}
        data-testid="motivation-context-toggle"
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="micro-label">{t.header}</span>
          {/* Motivation state badge — the new v2 contextual classifier */}
          <TooltipProvider delayDuration={140}>
            <Tooltip>
              <TooltipTrigger asChild>
                <span
                  data-testid="motivation-state-badge"
                  data-state={resolvedState}
                  className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10.5px] font-medium border tone-${stateMeta.tone}`}
                >
                  <StateIcon className="h-3 w-3" />
                  <span>{lang === 'en' ? stateMeta.label_en : stateMeta.label_es}</span>
                </span>
              </TooltipTrigger>
              <TooltipContent className="glass-surface text-xs max-w-[280px] leading-relaxed">
                {lang === 'en' ? stateMeta.hint_en : stateMeta.hint_es}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
          {/* Pressure state badge — competition-stage layer (final/knockout) */}
          {showPressure && (
            <TooltipProvider delayDuration={140}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span
                    data-testid="pressure-state-badge"
                    data-state={pressureState}
                    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10.5px] font-semibold border tone-${pressureMeta.tone}`}
                  >
                    <PressureIcon className="h-3 w-3" />
                    <span>{lang === 'en' ? pressureMeta.label_en : pressureMeta.label_es}</span>
                  </span>
                </TooltipTrigger>
                <TooltipContent className="glass-surface text-xs max-w-[280px] leading-relaxed">
                  {lang === 'en' ? pressureMeta.hint_en : pressureMeta.hint_es}
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}
          <div className="flex items-center gap-1.5">
            <MotivationBadge level={home.level || 3} lang={lang} />
            <span className="text-[10px] text-muted-foreground">vs</span>
            <MotivationBadge level={away.level || 3} lang={lang} />
          </div>
        </div>
        <ChevronDown className={`h-3.5 w-3.5 text-muted-foreground transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="grid md:grid-cols-2 gap-0 md:gap-0">
          {[{ side: 'home', data: home, name: homeName, reasons: homeReasons, impact: homeImpact },
            { side: 'away', data: away, name: awayName, reasons: awayReasons, impact: awayImpact }].map(({ side, data, name, reasons, impact }, idx) => (
            <div
              key={side}
              className={`p-4 ${idx === 0 ? 'md:border-r md:border-border/40' : ''}`}
              data-testid={`motivation-side-${side}`}
            >
              <div className="flex items-center justify-between gap-2 mb-3">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-[11px] uppercase tracking-wider text-muted-foreground">
                    {side === 'home' ? (lang === 'en' ? 'Home' : 'Local') : (lang === 'en' ? 'Away' : 'Visitante')}
                  </span>
                  <span className="font-semibold text-sm truncate">{name || (data?.label || '—')}</span>
                </div>
                <MotivationBadge level={data?.level || 3} lang={lang} />
              </div>

              {/* Reasons */}
              <div data-testid={`motivation-reasons-${side}`} className="mb-3">
                <div className="micro-label mb-1.5">{t.reasons}</div>
                {reasons.length > 0 ? (
                  <div className="flex flex-wrap gap-1.5">
                    {reasons.map((r) => {
                      const Icon = r.icon;
                      const label = lang === 'en' ? r.label_en : r.label_es;
                      return (
                        <span key={r.key} className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] border tone-${r.tone}`}>
                          <Icon className="h-3 w-3" />
                          <span>{label}</span>
                        </span>
                      );
                    })}
                  </div>
                ) : (
                  <p className="text-[11px] text-muted-foreground italic">{t.unknown}</p>
                )}
              </div>

              {/* Source quote — show the raw `reason` text from the analyst */}
              {data?.reason && (
                <div className="mb-3">
                  <div className="micro-label mb-1.5">{t.sources}</div>
                  <p className="text-[11.5px] text-muted-foreground leading-relaxed border-l-2 border-cyan-500/30 pl-2.5">
                    {data.reason}
                  </p>
                </div>
              )}

              {/* Impact (+/−) */}
              <div data-testid={`motivation-impact-${side}`}>
                <div className="micro-label mb-1.5">{t.impact}</div>
                <ul className="space-y-1">
                  {((lang === 'en' ? impact.positive_en : impact.positive_es) || []).map((line, i) => (
                    <li key={`p-${i}`} className="flex items-start gap-1.5 text-[11.5px] text-emerald-200/90">
                      <Plus className="h-3 w-3 mt-0.5 shrink-0" /><span>{line.replace(/^\+\s/, '')}</span>
                    </li>
                  ))}
                  {((lang === 'en' ? impact.negative_en : impact.negative_es) || []).map((line, i) => (
                    <li key={`n-${i}`} className="flex items-start gap-1.5 text-[11.5px] text-rose-300/90">
                      <Minus className="h-3 w-3 mt-0.5 shrink-0" /><span>{line.replace(/^−\s/, '')}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
