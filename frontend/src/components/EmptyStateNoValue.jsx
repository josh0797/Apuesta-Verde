import { motion } from 'framer-motion';
import { ShieldOff } from 'lucide-react';
import { useI18n } from '@/lib/i18n';

export function EmptyStateNoValue() {
  const { t } = useI18n();
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-6 md:p-8 flex flex-col items-center text-center"
      data-testid="empty-state-no-value"
    >
      <div className="h-12 w-12 rounded-full bg-amber-500/15 border border-amber-500/30 flex items-center justify-center mb-3">
        <ShieldOff className="h-6 w-6 text-amber-300" />
      </div>
      <h3 className="text-xl font-semibold text-amber-200">{t.dashboard.noValueTitle}</h3>
      <p className="text-sm text-amber-100/80 mt-2 max-w-md">{t.dashboard.noValueMsg}</p>
    </motion.div>
  );
}
