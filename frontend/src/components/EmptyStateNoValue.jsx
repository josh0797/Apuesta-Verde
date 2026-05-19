import { motion } from 'framer-motion';
import {
  ShieldOff, TrendingDown, AlertTriangle, Wallet, Lightbulb,
  Clock, Activity, BarChart3, Shield,
} from 'lucide-react';
import { useI18n } from '@/lib/i18n';

/**
 * EmptyStateCoaching — replaces a stark "no picks" message with an explainable
 * intelligence coach panel that:
 *   - Diagnoses the WHY (motivation, odds inflation, volatility, etc.)
 *   - Reinforces bankroll discipline ("Not betting is a winning decision")
 *   - Offers an educational tip aligned with the analyst persona
 *   - Suggests a waiting strategy with a concrete next window
 *
 * Backward-compatible export: the component is still default-exported as
 * EmptyStateNoValue so existing callers keep working.
 */

// Heuristic diagnosis based on the analyst summary (any list with content drives the message).
function diagnose(summary, lang) {
  if (!summary) return null;
  const motCount = (summary.discarded_motivation || []).length;
  const mktCount = (summary.discarded_market || []).length;
  const incCount = (summary.incomplete_data || []).length;
  const total = (summary.total_analyzed || 0);

  const items = [];
  if (motCount > 0) {
    items.push({
      icon: TrendingDown,
      tone: 'amber',
      label: lang === 'en'
        ? `${motCount} match${motCount > 1 ? 'es' : ''} with low motivation`
        : `${motCount} ${motCount > 1 ? 'partidos' : 'partido'} con motivación baja`,
      hint: lang === 'en'
        ? 'Teams with nothing to play for distort form signals.'
        : 'Equipos sin nada en juego distorsionan las señales de forma.',
    });
  }
  if (mktCount > 0) {
    items.push({
      icon: AlertTriangle,
      tone: 'rose',
      label: lang === 'en'
        ? `${mktCount} match${mktCount > 1 ? 'es' : ''} with suspicious odds`
        : `${mktCount} ${mktCount > 1 ? 'partidos' : 'partido'} con cuotas sospechosas`,
      hint: lang === 'en'
        ? 'Inflated lines or single-snapshot odds: market not stable.'
        : 'Líneas infladas o snapshot único: mercado inestable.',
    });
  }
  if (incCount > 0) {
    items.push({
      icon: BarChart3,
      tone: 'slate',
      label: lang === 'en'
        ? `${incCount} match${incCount > 1 ? 'es' : ''} with incomplete data`
        : `${incCount} ${incCount > 1 ? 'partidos' : 'partido'} con datos incompletos`,
      hint: lang === 'en'
        ? 'Without odds + form + position the engine waits.'
        : 'Sin odds + forma + posición el motor espera.',
    });
  }
  if (items.length === 0 && total === 0) {
    items.push({
      icon: Clock,
      tone: 'slate',
      label: lang === 'en' ? 'No matches in the next 48h window' : 'No hay partidos en la ventana de 48h',
      hint: lang === 'en' ? 'Check back closer to kick-off time.' : 'Vuelve más cerca de la hora del partido.',
    });
  }
  return items;
}

function suggestNext(lang) {
  return lang === 'en'
    ? {
        title: 'Suggested strategy',
        rows: [
          'Wait 1–2 hours for odds to stabilize.',
          'Recheck top leagues 90 min before kick-off — sharpest market.',
          'Skip the slate and preserve bankroll for higher-quality windows.',
        ],
      }
    : {
        title: 'Estrategia sugerida',
        rows: [
          'Espera 1–2 horas a que se estabilicen las cuotas.',
          'Revisa ligas top 90 min antes del partido — mercado más afilado.',
          'Salta la jornada y preserva bankroll para ventanas de mayor calidad.',
        ],
      };
}

function eduTip(lang) {
  return lang === 'en'
    ? 'Tip: in a low-value slate, the disciplined edge is to skip — not to lower confidence thresholds.'
    : 'Tip: en una jornada sin valor, el edge disciplinado es saltarse — no bajar el umbral de confianza.';
}

export function EmptyStateNoValue({ summary }) {
  const { t, lang } = useI18n();
  const items = diagnose(summary, lang) || [];
  const strategy = suggestNext(lang);

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="rounded-xl border border-border bg-card/60 overflow-hidden noise-overlay"
      data-testid="empty-state-no-value"
    >
      {/* Header band */}
      <div className="px-5 md:px-6 pt-5 md:pt-6 pb-3 flex items-start gap-4">
        <div className="h-11 w-11 shrink-0 rounded-xl border border-amber-500/30 bg-amber-500/10 flex items-center justify-center">
          <ShieldOff className="h-5 w-5 text-amber-300" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="micro-label mb-1">ENGINE REASONING</div>
          <h3 className="text-lg md:text-xl font-semibold tracking-tight" data-testid="empty-state-title">
            {t.dashboard.noValueTitle}
          </h3>
          <p className="text-[13px] text-muted-foreground mt-1 max-w-xl leading-relaxed" data-testid="empty-state-message">
            {t.dashboard.noValueMsg}
          </p>
        </div>
        <span className="hidden md:inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-emerald-500/30 bg-emerald-500/5 text-emerald-200 text-[11px] font-medium">
          <Wallet className="h-3 w-3" />
          {lang === 'en' ? 'Bankroll preserved' : 'Bankroll preservado'}
        </span>
      </div>

      {/* WHY diagnosis */}
      {items.length > 0 && (
        <div className="px-5 md:px-6 pb-4">
          <div className="micro-label mb-2">{lang === 'en' ? 'WHY THE ENGINE SKIPPED' : 'POR QUÉ EL MOTOR SE ABSTUVO'}</div>
          <div className="grid sm:grid-cols-2 gap-2" data-testid="empty-state-diagnosis">
            {items.map((it, i) => {
              const Icon = it.icon;
              return (
                <div
                  key={i}
                  className={`flex items-start gap-2.5 p-3 rounded-lg border bg-background/30 tone-${it.tone}`}
                >
                  <Icon className="h-4 w-4 shrink-0 mt-0.5" />
                  <div className="min-w-0">
                    <div className="text-[12.5px] font-medium leading-snug">{it.label}</div>
                    <div className="text-[11px] text-muted-foreground mt-0.5 leading-snug">{it.hint}</div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Strategy */}
      <div className="px-5 md:px-6 pb-4">
        <div className="micro-label mb-2 flex items-center gap-1.5">
          <Activity className="h-3 w-3" />{strategy.title}
        </div>
        <ul className="space-y-1.5" data-testid="empty-state-strategy">
          {strategy.rows.map((row, i) => (
            <li key={i} className="flex items-start gap-2 text-[12.5px] text-muted-foreground">
              <span className="mt-1.5 h-1 w-1 rounded-full bg-cyan-400 shrink-0" />
              <span>{row}</span>
            </li>
          ))}
        </ul>
      </div>

      {/* Bankroll discipline footer */}
      <div className="border-t border-border/50 bg-background/40 px-5 md:px-6 py-3 flex items-start gap-2.5">
        <Lightbulb className="h-4 w-4 text-cyan-300 shrink-0 mt-0.5" />
        <p className="text-[12px] text-muted-foreground leading-relaxed" data-testid="empty-state-edu-tip">
          {eduTip(lang)}{' '}
          <span className="text-foreground font-medium">
            {lang === 'en' ? 'Not betting is also a winning decision.' : 'No apostar también es una decisión ganadora.'}
          </span>
        </p>
      </div>
    </motion.div>
  );
}
