import { Activity, Users, AlertTriangle, TrendingUp, Gauge } from 'lucide-react';

/**
 * MLBMatchupPanel — Renders the structural matchup analysis the backend
 * computes from the MLB Stats API:
 *   • Probable pitchers + ERA/WHIP/K-BB/HR/IP
 *   • Bullpen risk (3-day fatigue score)
 *   • Offensive pressure (OPS, runs/G)
 *   • Aggregate structural edge side + narrative
 *
 * Only renders when sport === 'baseball' AND match.mlb_matchup is present.
 */
export function MLBMatchupPanel({ matchup, lang = 'es' }) {
  if (!matchup || !matchup.available) return null;

  const raw = matchup.raw || {};
  const homePitcher = raw.home_pitcher || {};
  const awayPitcher = raw.away_pitcher || {};
  const homeBatting = raw.home_batting || {};
  const awayBatting = raw.away_batting || {};
  const homeBullpen = raw.home_bullpen || {};
  const awayBullpen = raw.away_bullpen || {};

  const dataQuality = matchup.data_quality;
  const qualityLabel = lang === 'en'
    ? (dataQuality === 'full' ? 'Full data' : dataQuality === 'partial' ? 'Partial data' : 'Missing data')
    : (dataQuality === 'full' ? 'Datos completos' : dataQuality === 'partial' ? 'Datos parciales' : 'Sin datos');
  const qualityTone = dataQuality === 'full' ? 'text-emerald-300'
    : dataQuality === 'partial' ? 'text-amber-300'
    : 'text-red-300';

  return (
    <section className="rounded-xl border border-border bg-card p-4 md:p-5 space-y-4" data-testid="mlb-matchup-panel">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-cyan-300" />
          <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            {lang === 'en' ? 'MLB Structural Matchup' : 'Matchup estructural MLB'}
          </h3>
        </div>
        <span className={`text-[10px] uppercase tracking-wide font-mono-tabular ${qualityTone}`} data-testid="mlb-data-quality">
          {qualityLabel}
        </span>
      </div>

      {/* Narrative + structural edge */}
      <div className="rounded-lg border border-border/60 bg-secondary/30 p-3" data-testid="mlb-narrative">
        <p className="text-sm leading-relaxed">{matchup.narrative}</p>
        {matchup.structural_edge_side && matchup.structural_edge_side !== 'even' && (
          <div className="text-[11px] mt-2 inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md border border-cyan-500/30 bg-cyan-500/10 text-cyan-200">
            <TrendingUp className="h-3 w-3" />
            {lang === 'en' ? 'Structural edge' : 'Edge estructural'}: <span className="font-semibold">{matchup.structural_edge_side}</span>
            <span className="text-[10px] opacity-70 ml-1 font-mono-tabular">({(matchup.structural_edge_strength * 100).toFixed(0)}%)</span>
          </div>
        )}
      </div>

      {/* Pitchers */}
      <div data-testid="mlb-pitchers-block">
        <div className="micro-label mb-2 flex items-center gap-1.5">
          <Gauge className="h-3 w-3" />
          {lang === 'en' ? 'Probable Pitchers' : 'Pitchers probables'}
          {matchup.pitcher_advantage && matchup.pitcher_advantage !== 'even' && (
            <span className="ml-1 text-[10px] text-emerald-300">
              ({lang === 'en' ? 'edge' : 'ventaja'} {matchup.pitcher_advantage})
            </span>
          )}
        </div>
        <div className="grid sm:grid-cols-2 gap-3">
          <PitcherCard
            label={lang === 'en' ? 'Home' : 'Local'}
            name={raw.home_probable}
            stats={homePitcher}
            score={matchup.home_pitcher_score}
            highlighted={matchup.pitcher_advantage === 'home'}
            testId="mlb-home-pitcher"
            lang={lang}
          />
          <PitcherCard
            label={lang === 'en' ? 'Away' : 'Visitante'}
            name={raw.away_probable}
            stats={awayPitcher}
            score={matchup.away_pitcher_score}
            highlighted={matchup.pitcher_advantage === 'away'}
            testId="mlb-away-pitcher"
            lang={lang}
          />
        </div>
      </div>

      {/* Bullpen */}
      <div data-testid="mlb-bullpen-block">
        <div className="micro-label mb-2 flex items-center gap-1.5">
          <Users className="h-3 w-3" />
          {lang === 'en' ? 'Bullpen Risk (last 3 days)' : 'Riesgo de bullpen (últimos 3 días)'}
        </div>
        <div className="grid sm:grid-cols-2 gap-3">
          <BullpenCard side={lang === 'en' ? 'Home' : 'Local'} bullpen={homeBullpen} risk={matchup.home_bullpen_risk} testId="mlb-home-bullpen" lang={lang} />
          <BullpenCard side={lang === 'en' ? 'Away' : 'Visitante'} bullpen={awayBullpen} risk={matchup.away_bullpen_risk} testId="mlb-away-bullpen" lang={lang} />
        </div>
      </div>

      {/* Offense */}
      <div data-testid="mlb-offense-block">
        <div className="micro-label mb-2 flex items-center gap-1.5">
          <TrendingUp className="h-3 w-3" />
          {lang === 'en' ? 'Offensive Form' : 'Forma ofensiva'}
        </div>
        <div className="grid sm:grid-cols-2 gap-3">
          <OffenseCard side={lang === 'en' ? 'Home' : 'Local'} batting={homeBatting} score={matchup.home_offense_score} highlighted={matchup.offensive_pressure_side === 'home'} testId="mlb-home-offense" lang={lang} />
          <OffenseCard side={lang === 'en' ? 'Away' : 'Visitante'} batting={awayBatting} score={matchup.away_offense_score} highlighted={matchup.offensive_pressure_side === 'away'} testId="mlb-away-offense" lang={lang} />
        </div>
      </div>

      {dataQuality === 'missing' && (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/5 text-amber-200 text-[11px] px-3 py-2 flex items-start gap-1.5" data-testid="mlb-missing-data-hint">
          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <span>
            {lang === 'en'
              ? 'MLB structural data not available for this game. The LLM analysis was performed without pitcher/bullpen/batting hydration.'
              : 'Datos estructurales MLB no disponibles para este juego. El análisis LLM se hizo sin hidratación de pitcher/bullpen/bateo.'}
          </span>
        </div>
      )}
    </section>
  );
}

function PitcherCard({ label, name, stats, score, highlighted, testId, lang }) {
  const border = highlighted ? 'border-emerald-500/40 bg-emerald-500/5' : 'border-border bg-secondary/30';
  return (
    <div className={`rounded-lg border p-3 ${border}`} data-testid={testId}>
      <div className="flex items-center justify-between gap-2">
        <div className="text-[10px] uppercase tracking-wide opacity-70">{label}</div>
        {score != null && (
          <span className="text-[10px] font-mono-tabular text-cyan-300">
            {lang === 'en' ? 'Score' : 'Puntaje'} {(score * 100).toFixed(0)}
          </span>
        )}
      </div>
      <div className="text-sm font-semibold mt-0.5 truncate">
        {name || (lang === 'en' ? 'TBD' : 'Por anunciar')}
        {stats.hand && <span className="ml-1 text-[10px] text-muted-foreground">({stats.hand}HP)</span>}
      </div>
      <div className="grid grid-cols-3 gap-x-2 gap-y-1 mt-2 text-[11px]">
        <Stat label="ERA" value={fmt(stats.era)} />
        <Stat label="WHIP" value={fmt(stats.whip)} />
        <Stat label="K/BB" value={fmt(stats.k_per_bb)} />
        <Stat label={lang === 'en' ? 'HR/9' : 'HR/9'} value={fmt(stats.hr_per_9)} />
        <Stat label="IP" value={fmt(stats.innings_pitched, 1)} />
        <Stat label={lang === 'en' ? 'GS' : 'JL'} value={stats.games_pitched ?? '—'} />
      </div>
    </div>
  );
}

function BullpenCard({ side, bullpen, risk, testId, lang }) {
  if (!bullpen || bullpen.fatigue_score_0_100 == null) {
    return (
      <div className="rounded-lg border border-border bg-secondary/30 p-3 opacity-70" data-testid={testId}>
        <div className="text-[10px] uppercase tracking-wide opacity-70">{side}</div>
        <div className="text-xs text-muted-foreground mt-1">{lang === 'en' ? 'No data' : 'Sin datos'}</div>
      </div>
    );
  }
  const fatigue = bullpen.fatigue_score_0_100 || 0;
  const label = bullpen.fatigue_label;
  const tone = fatigue < 30 ? 'text-emerald-300' : fatigue < 60 ? 'text-amber-300' : 'text-red-300';
  return (
    <div className="rounded-lg border border-border bg-secondary/30 p-3" data-testid={testId}>
      <div className="text-[10px] uppercase tracking-wide opacity-70">{side}</div>
      <div className={`text-sm font-semibold mt-0.5 capitalize ${tone}`}>
        {lang === 'en' ? (label || 'unknown') : (
          { fresh: 'descansado', moderate: 'moderado', high: 'cansado', extreme: 'extremo' }[label] || 'desconocido'
        )}
        <span className="text-[10px] text-muted-foreground ml-1 font-mono-tabular">{fatigue}/100</span>
      </div>
      <div className="text-[11px] text-muted-foreground mt-1.5">
        {lang === 'en' ? 'Games played: ' : 'Juegos recientes: '}
        <span className="text-foreground mono font-mono-tabular">{bullpen.games_played_recent ?? 0}</span>
        {bullpen.extra_inning_games_recent > 0 && (
          <span className="ml-2 text-amber-300">
            +{bullpen.extra_inning_games_recent} {lang === 'en' ? 'extra-innings' : 'extra innings'}
          </span>
        )}
      </div>
    </div>
  );
}

function OffenseCard({ side, batting, score, highlighted, testId, lang }) {
  const border = highlighted ? 'border-emerald-500/40 bg-emerald-500/5' : 'border-border bg-secondary/30';
  return (
    <div className={`rounded-lg border p-3 ${border}`} data-testid={testId}>
      <div className="flex items-center justify-between gap-2">
        <div className="text-[10px] uppercase tracking-wide opacity-70">{side}</div>
        {score != null && (
          <span className="text-[10px] font-mono-tabular text-cyan-300">
            {(score * 100).toFixed(0)}
          </span>
        )}
      </div>
      <div className="grid grid-cols-3 gap-x-2 gap-y-1 mt-2 text-[11px]">
        <Stat label={lang === 'en' ? 'R/G' : 'C/J'} value={fmt(batting.runs_per_game)} />
        <Stat label="OPS" value={fmt(batting.ops, 3)} />
        <Stat label="OBP" value={fmt(batting.obp, 3)} />
        <Stat label="SLG" value={fmt(batting.slg, 3)} />
        <Stat label={lang === 'en' ? 'BB%' : 'BB%'} value={pct(batting.walk_rate)} />
        <Stat label={lang === 'en' ? 'K%' : 'K%'} value={pct(batting.strikeout_rate)} />
      </div>
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="flex items-center justify-between gap-1">
      <span className="text-muted-foreground">{label}</span>
      <span className="mono font-mono-tabular text-foreground">{value}</span>
    </div>
  );
}

function fmt(v, decimals = 2) {
  if (v == null || v === '') return '—';
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return n.toFixed(decimals);
}

function pct(v) {
  if (v == null || v === '') return '—';
  const n = Number(v);
  if (Number.isNaN(n)) return '—';
  return `${(n * 100).toFixed(1)}%`;
}
