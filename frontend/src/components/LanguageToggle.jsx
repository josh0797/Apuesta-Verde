import { useI18n } from '@/lib/i18n';

export function LanguageToggle() {
  const { lang, setLang } = useI18n();
  return (
    <div className="inline-flex rounded-md border border-border bg-card/60 backdrop-blur p-0.5" data-testid="language-toggle">
      <button
        onClick={() => setLang('es')}
        data-testid="lang-es-btn"
        className={`px-2.5 py-1 text-xs font-medium rounded-[6px] transition-colors ${lang === 'es' ? 'bg-secondary text-foreground' : 'text-muted-foreground hover:text-foreground'}`}
      >ES</button>
      <button
        onClick={() => setLang('en')}
        data-testid="lang-en-btn"
        className={`px-2.5 py-1 text-xs font-medium rounded-[6px] transition-colors ${lang === 'en' ? 'bg-secondary text-foreground' : 'text-muted-foreground hover:text-foreground'}`}
      >EN</button>
    </div>
  );
}
