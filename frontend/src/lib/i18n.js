import { createContext, useContext, useState, useEffect, useCallback } from 'react';

const STRINGS = {
  es: {
    appName: 'Value Bet Intelligence',
    tagline: 'Disciplina. Transparencia. Valor real.',
    login: { title: 'Inicia sesión', subtitle: 'Accede a tus picks y estadísticas', emailLabel: 'Email', passwordLabel: 'Contraseña', loginBtn: 'Entrar', registerBtn: 'Crear cuenta', toggleToRegister: '¿No tienes cuenta? Registrarse', toggleToLogin: '¿Ya tienes cuenta? Entrar', nameLabel: 'Nombre (opcional)', useDemo: 'Usar cuenta demo', demoNote: 'Demo: demo@valuebet.app / demo1234', errorGeneric: 'No se pudo procesar la solicitud', securityNote: 'Tus credenciales viajan cifradas. No compartimos tu información.', bullet1: 'Solo mercados de baja volatilidad', bullet2: 'Detector anti-trampa de cuotas', bullet3: 'Tracking de aciertos personal' },
    nav: { dashboard: 'Dashboard', live: 'En Vivo', history: 'Historial', profile: 'Perfil' },
    dashboard: { title: 'Picks del día', subtitle: 'Análisis de valor para las próximas 48 horas', generateBtn: 'Generar picks del día', recalibrateBtn: 'Recalibrar', recalibrating: 'Recalibrando…', recalibrateHint: 'Re-aplica los nuevos modelos a los picks ya generados sin re-ingestar', recalibrateNoRun: 'Primero genera picks del día para poder recalibrar', recalibrateOnlySupported: 'Recalibrar solo disponible para MLB y Basketball por ahora', recalibrateDone: 'Picks recalibrados ✓', refreshingData: 'Actualizando datos…', running: 'Analizando partidos…', lastRun: 'Última ejecución', noRunYet: 'Aún no has generado picks. Pulsa el botón para correr el análisis.', groupHigh: 'Alta confianza', groupMedium: 'Confianza media', groupDiscMotivation: 'Descartados por motivación', groupDiscMarket: 'Descartados por mercado frágil', groupIncomplete: 'Datos incompletos', summary: 'Resumen', analyzed: 'Analizados', recommended: 'Recomendados', discarded: 'Descartados', noValueTitle: 'Hoy no hay valor', noValueMsg: 'No apostar es la mejor apuesta. Mejor preservar el bankroll que forzar picks débiles.', exportCsv: 'Exportar CSV', filtersTitle: 'Filtros', filterLeague: 'Liga', filterMarket: 'Mercado', filterMinConfidence: 'Confianza mín.', filterAll: 'Todas', filterReset: 'Limpiar', filteredOf: 'mostrando {kept} de {total}', detailsTitle: 'Detalle del análisis', openDetails: 'ver detalle ↓', savePending: 'Marcar para seguir', alreadyPending: 'Pendiente — verás este pick en Historial', savedAsPending: 'Pick guardado como pendiente. Lo liquidas desde Historial.', refreshMatchesBtn: 'Refrescar partidos', refreshingMatches: 'Refrescando…', refreshMatchesHint: 'Reingesta de fixtures + cuotas sin disparar análisis LLM', refreshMatchesDone: 'Partidos actualizados ({delta} nuevos, {total} totales)', refreshMatchesError: 'No se pudieron refrescar los partidos', nationalTeamsBtn: 'Selecciones nacionales', nationalTeamsHint: 'Analizar solo torneos de selecciones (Mundial, Eurocopa, Copa América, Nations League, eliminatorias, amistosos)' },
    live: { title: 'Partidos en vivo', subtitle: 'Reevaluación en tiempo real con live stats', noLive: 'No hay partidos en vivo en este momento.', minute: 'min', possession: 'Posesión', shots: 'Tiros', shotsOn: 'A puerta', corners: 'Córners', xg: 'xG', bigFiveOnly: 'Solo 5 grandes', showAll: 'Ver todas', filteredHint: '{hidden} partidos ocultos fuera de las 5 grandes.', noLiveBigFive: 'No hay partidos en vivo en las 5 grandes ahora. Activa "Ver todas" para ver el resto.', mlbOnly: 'Solo MLB', filteredHintMlb: '{hidden} juegos ocultos fuera de MLB.', noLiveMlb: 'No hay juegos MLB en vivo ahora. Activa "Ver todas" para ver el resto.' },
    match: { backToList: 'Volver', confidenceMeter: 'Score de Confianza', recommendation: 'Apuesta recomendada', market: 'Mercado', selection: 'Selección', oddsRange: 'Cuota aprox.', reasoning: 'Razonamiento', risks: 'Riesgos identificados', cashOut: 'Cash Out', motivationCtx: 'Contexto motivacional', keyData: 'Datos clave', form: 'Forma', goalsForAvg: 'Goles a favor (prom.)', goalsAgainstAvg: 'Goles en contra (prom.)', injuries: 'Lesiones', position: 'Posición', points: 'Puntos', oddsTable: 'Cuotas (comparativa)', bookmaker: 'Casa', home: 'Local', draw: 'Empate', away: 'Visitante', lineMovement: 'Movimiento de línea', estable: 'Estable', subiendo: 'Subiendo', bajando: 'Bajando', desconocido: 'Desconocido', noLLMPick: 'Este partido no fue analizado en la última ejecución.', generateForMatch: 'Genera picks del día para verlo analizado.', actions: 'Acciones', markWon: 'Gané', markLost: 'Perdí', markPush: 'Push', notRecommended: 'No recomendado', recommendedMarket: 'Mercado recomendado', dataIncomplete: 'Datos incompletos', headToHead: 'Cara a cara reciente', livePill: 'EN VIVO', upcomingPill: 'PRÓXIMO', kickoff: 'Pitazo inicial' },
    history: { title: 'Historial de picks', subtitle: 'Tracking personal de aciertos', winRate: 'Winrate', settled: 'Decididos', streak: 'Racha', last10: 'Últimos 10', byTier: 'Por nivel de confianza', empty: 'Aún no has marcado picks. Márcalos como pendientes desde el Dashboard o liquídalos desde el detalle del partido.', evolution: 'Evolución del winrate', exportCsv: 'Exportar CSV', roiTitle: 'Calculadora ROI', stakeLabel: 'Stake por pick', roiTotalWagered: 'Apostado', roiNetProfit: 'Ganancia neta', roiPct: 'ROI', roiAvgWonOdds: 'Cuota media ganada', roiAvgLostOdds: 'Cuota media perdida', settledWithOdds: 'con cuota', roiHint: 'ROI calculado solo sobre picks con cuota registrada. Algunos picks antiguos pueden no tenerla.', markWon: 'Gané', markLost: 'Perdí', markPush: 'Push', settlePick: 'Liquidar', outcomePending: 'Pendiente', actions: 'Acciones', settledOk: 'Pick liquidado', settleError: 'No se pudo liquidar el pick' },
    profile: { title: 'Perfil', signOut: 'Cerrar sesión', language: 'Idioma', emailLabel: 'Email', joinedOn: 'Miembro desde', stats: 'Estadísticas globales', upcomingPlaceholder: 'ROI próximamente.', system: 'Sistema', scheduler: 'Scheduler', providers: 'Proveedores LLM', schedulerEnabled: 'Activado', schedulerDisabled: 'Desactivado', nextRun: 'Próx. ejecución' },
    freshness: { fresh: 'datos frescos', stale: 'datos antiguos', missing: 'sin datos' },
    confidence: { Maxima: 'Máxima', Alta: 'Alta', Media: 'Media' },
    common: { loading: 'Cargando…', error: 'Error', retry: 'Reintentar', save: 'Guardar', cancel: 'Cancelar', open: 'Abrir', close: 'Cerrar', all: 'Todos' },
    sport: { label: 'Deporte', football: 'Fútbol', basketball: 'NBA / Basket', baseball: 'MLB / Béisbol' }
  },
  en: {
    appName: 'Value Bet Intelligence',
    tagline: 'Discipline. Transparency. Real value.',
    login: { title: 'Sign in', subtitle: 'Access your picks and stats', emailLabel: 'Email', passwordLabel: 'Password', loginBtn: 'Sign in', registerBtn: 'Create account', toggleToRegister: "Don't have an account? Register", toggleToLogin: 'Already a member? Sign in', nameLabel: 'Name (optional)', useDemo: 'Use demo account', demoNote: 'Demo: demo@valuebet.app / demo1234', errorGeneric: 'Could not process request', securityNote: 'Your credentials are sent encrypted. We never share your info.', bullet1: 'Low-volatility markets only', bullet2: 'Anti-trap odds detector', bullet3: 'Personal accuracy tracking' },
    nav: { dashboard: 'Dashboard', live: 'Live', history: 'History', profile: 'Profile' },
    dashboard: { title: "Today's picks", subtitle: 'Value analysis for the next 48 hours', generateBtn: "Generate today's picks", recalibrateBtn: 'Recalibrate', recalibrating: 'Recalibrating…', recalibrateHint: "Re-apply the latest models to today's picks without re-ingesting", recalibrateNoRun: 'Generate picks first so we have something to recalibrate', recalibrateOnlySupported: 'Recalibrate only available for MLB and Basketball for now', recalibrateDone: 'Picks recalibrated ✓', refreshingData: 'Refreshing data…', running: 'Analyzing matches…', lastRun: 'Last run', noRunYet: 'No picks generated yet. Hit the button to run the analyst.', groupHigh: 'High confidence', groupMedium: 'Medium confidence', groupDiscMotivation: 'Discarded — motivation', groupDiscMarket: 'Discarded — fragile market', groupIncomplete: 'Incomplete data', summary: 'Summary', analyzed: 'Analyzed', recommended: 'Recommended', discarded: 'Discarded', noValueTitle: 'No value today', noValueMsg: 'Not betting is the best bet. Better preserve bankroll than force weak picks.', exportCsv: 'Export CSV', filtersTitle: 'Filters', filterLeague: 'League', filterMarket: 'Market', filterMinConfidence: 'Min confidence', filterAll: 'All', filterReset: 'Reset', filteredOf: 'showing {kept} of {total}', detailsTitle: 'Analysis details', openDetails: 'view details ↓', savePending: 'Track this pick', alreadyPending: 'Pending — find it in History', savedAsPending: 'Pick saved as pending. Settle it later from History.', refreshMatchesBtn: 'Refresh matches', refreshingMatches: 'Refreshing…', refreshMatchesHint: 'Re-ingest fixtures + odds without firing the LLM analyst', refreshMatchesDone: 'Matches refreshed ({delta} new, {total} total)', refreshMatchesError: 'Could not refresh matches', nationalTeamsBtn: 'National teams', nationalTeamsHint: 'Analyze national-team tournaments only (World Cup, Euros, Copa America, Nations League, qualifiers, friendlies)' },
    live: { title: 'Live matches', subtitle: 'Real-time re-evaluation with live stats', noLive: 'No live matches right now.', minute: "'", possession: 'Possession', shots: 'Shots', shotsOn: 'On target', corners: 'Corners', xg: 'xG', bigFiveOnly: 'Big Five only', showAll: 'Show all', filteredHint: '{hidden} matches outside the Big Five are hidden.', noLiveBigFive: 'No Big Five matches live right now. Toggle "Show all" to see the rest.', mlbOnly: 'MLB only', filteredHintMlb: '{hidden} non-MLB games hidden.', noLiveMlb: 'No MLB games live right now. Toggle "Show all" to see the rest.' },
    match: { backToList: 'Back', confidenceMeter: 'Confidence Score', recommendation: 'Recommended bet', market: 'Market', selection: 'Selection', oddsRange: 'Approx. odds', reasoning: 'Reasoning', risks: 'Identified risks', cashOut: 'Cash Out', motivationCtx: 'Motivational context', keyData: 'Key data', form: 'Form', goalsForAvg: 'Goals for (avg.)', goalsAgainstAvg: 'Goals against (avg.)', injuries: 'Injuries', position: 'Position', points: 'Points', oddsTable: 'Odds (comparison)', bookmaker: 'Bookmaker', home: 'Home', draw: 'Draw', away: 'Away', lineMovement: 'Line movement', estable: 'Stable', subiendo: 'Rising', bajando: 'Falling', desconocido: 'Unknown', noLLMPick: 'This match was not analyzed in the latest run.', generateForMatch: "Generate today's picks to see the analysis.", actions: 'Actions', markWon: 'Won', markLost: 'Lost', markPush: 'Push', notRecommended: 'Not recommended', recommendedMarket: 'Recommended market', dataIncomplete: 'Incomplete data', headToHead: 'Recent head-to-head', livePill: 'LIVE', upcomingPill: 'UPCOMING', kickoff: 'Kickoff' },
    history: { title: 'Pick history', subtitle: 'Personal accuracy tracking', winRate: 'Win rate', settled: 'Settled', streak: 'Streak', last10: 'Last 10', byTier: 'By confidence tier', empty: 'No tracked picks yet. Mark them as pending from the Dashboard, or settle them from match detail.', evolution: 'Win rate evolution', exportCsv: 'Export CSV', roiTitle: 'ROI Calculator', stakeLabel: 'Stake per pick', roiTotalWagered: 'Wagered', roiNetProfit: 'Net profit', roiPct: 'ROI', roiAvgWonOdds: 'Avg won odds', roiAvgLostOdds: 'Avg lost odds', settledWithOdds: 'with odds', roiHint: 'ROI computed only on picks with recorded odds. Older picks may not have them.', markWon: 'Won', markLost: 'Lost', markPush: 'Push', settlePick: 'Settle', outcomePending: 'Pending', actions: 'Actions', settledOk: 'Pick settled', settleError: 'Could not settle the pick' },
    profile: { title: 'Profile', signOut: 'Sign out', language: 'Language', emailLabel: 'Email', joinedOn: 'Member since', stats: 'Overall stats', upcomingPlaceholder: 'ROI coming soon.', system: 'System', scheduler: 'Scheduler', providers: 'LLM Providers', schedulerEnabled: 'Enabled', schedulerDisabled: 'Disabled', nextRun: 'Next run' },
    freshness: { fresh: 'fresh data', stale: 'stale data', missing: 'missing data' },
    confidence: { Maxima: 'Max', Alta: 'High', Media: 'Medium' },
    common: { loading: 'Loading…', error: 'Error', retry: 'Retry', save: 'Save', cancel: 'Cancel', open: 'Open', close: 'Close', all: 'All' },
    sport: { label: 'Sport', football: 'Football', basketball: 'NBA / Basketball', baseball: 'MLB / Baseball' }
  }
};

const I18nContext = createContext({ lang: 'es', t: STRINGS.es, setLang: () => {} });

/**
 * Sport-aware vocabulary. Returns the right unit/event noun for the active sport
 * so we don't have to hard-code "partido / goles" everywhere.
 *
 * Usage:
 *   const terms = sportTerms(lang, sport);
 *   terms.eventPlural  // "partidos" / "juegos" / "encuentros"
 *   terms.scoreUnit    // "goles" / "puntos" / "carreras"
 */
const SPORT_TERMS = {
  es: {
    football:   { event: 'partido',  eventPlural: 'partidos',  scoreUnit: 'goles',    scoreUnitSingular: 'gol',    scorer: 'goleador',   period: 'tiempo',  periodPlural: 'tiempos' },
    basketball: { event: 'juego',    eventPlural: 'juegos',    scoreUnit: 'puntos',   scoreUnitSingular: 'punto',  scorer: 'anotador',   period: 'cuarto',  periodPlural: 'cuartos' },
    baseball:   { event: 'juego',    eventPlural: 'juegos',    scoreUnit: 'carreras', scoreUnitSingular: 'carrera', scorer: 'bateador',  period: 'entrada', periodPlural: 'entradas' },
  },
  en: {
    football:   { event: 'match',  eventPlural: 'matches', scoreUnit: 'goals',  scoreUnitSingular: 'goal',  scorer: 'scorer',  period: 'half',    periodPlural: 'halves' },
    basketball: { event: 'game',   eventPlural: 'games',   scoreUnit: 'points', scoreUnitSingular: 'point', scorer: 'scorer',  period: 'quarter', periodPlural: 'quarters' },
    baseball:   { event: 'game',   eventPlural: 'games',   scoreUnit: 'runs',   scoreUnitSingular: 'run',   scorer: 'batter',  period: 'inning',  periodPlural: 'innings' },
  },
};

export function sportTerms(lang, sport) {
  const byLang = SPORT_TERMS[lang] || SPORT_TERMS.es;
  return byLang[sport] || byLang.football;
}

export function I18nProvider({ children }) {
  const [lang, setLangState] = useState(() => localStorage.getItem('vbi_lang') || 'es');
  const setLang = useCallback((l) => {
    setLangState(l);
    localStorage.setItem('vbi_lang', l);
  }, []);
  useEffect(() => { document.documentElement.lang = lang; }, [lang]);
  const value = { lang, t: STRINGS[lang] || STRINGS.es, setLang };
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() { return useContext(I18nContext); }
