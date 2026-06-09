/**
 * OffensiveInjuryImpactPanel — MLB-specific.
 *
 * Replaces the generic "X jugadores lesionados" counter with a
 * quality-weighted assessment of the offensive damage. Reads from
 * `match.offensive_injury_impact` produced by
 *   services/mlb_offensive_injury_impact.py::compute_offensive_injury_impact
 *
 * Observe-only: NEVER mutates engine pick polarity.
 *
 * Expected payload contract (per team):
 *   {
 *     available, team, offensive_injury_score, impact_bucket,
 *     missing_top5_count, top5_missing: [{name, position, score, ops, hr, runs, rbi, wrc_plus, fallback}],
 *     top5_available: [...], run_creation_lost_estimate, reason_codes,
 *   }
 *
 * Matchup wrapper:
 *   { home, away, imbalance, favors_team, under_support, narrative_es, reason_codes }
 */
import { useState } from 'react';
import {
  ChevronDown, ChevronUp, UserMinus, Activity, TrendingDown, Info,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';

const BUCKET_LABELS = {
  LOW:    { es: 'Bajo',  en: 'Low'    },
  MEDIUM: { es: 'Medio', en: 'Medium' },
  HIGH:   { es: 'Alto',  en: 'High'   },
};

// Color palette per user spec:
//   LOW    → emerald/green (neutral, healthy)
//   MEDIUM → amber (warning)
//   HIGH   → rose (destructive)
const BUCKET_TONE = {
  LOW:    'border-emerald-500/30 bg-emerald-500/5  text-emerald-200',
  MEDIUM: 'border-amber-500/40   bg-amber-500/10   text-amber-200',
  HIGH:   'border-rose-500/40    bg-rose-500/10    text-rose-200',
};

const BUCKET_DOT = {
  LOW:    'bg-emerald-400',
  MEDIUM: 'bg-amber-400',
  HIGH:   'bg-rose-400',
};

function fmtNum(v, digits = 0) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  return digits > 0 ? n.toFixed(digits) : Math.round(n).toString();
}

function fmtOps(v) {
  if (v == null || Number.isNaN(Number(v))) return null;
  // "0.872" → ".872" (MLB convention)
  return Number(v).toFixed(3).replace(/^0/, '');
}

export function OffensiveInjuryImpactPanel({
  impact,
  lang = 'es',
  testId,
}) {
  const [open, setOpen] = useState(false);

  if (!impact || impact.available === false) return null;

  const home = impact.home || {};
  const away = impact.away || {};
  const homeBucket = home.impact_bucket || 'LOW';
  const awayBucket = away.impact_bucket || 'LOW';

  // Most-severe-side dictates the panel's outer tone.
  const sideOrder = { LOW: 0, MEDIUM: 1, HIGH: 2 };
  const worstBucket = sideOrder[awayBucket] > sideOrder[homeBucket]
    ? awayBucket : homeBucket;
  const outerTone = BUCKET_TONE[worstBucket] || BUCKET_TONE.LOW;

  const homeMissing = Array.isArray(home.top5_missing) ? home.top5_missing : [];
  const awayMissing = Array.isArray(away.top5_missing) ? away.top5_missing : [];
  const anyMissing  = homeMissing.length > 0 || awayMissing.length > 0;

  // Nothing to surface: both teams healthy and no narrative.
  if (!anyMissing && !impact.narrative_es) return null;

  const baseId = testId || 'offensive-injury-impact-panel';

  return (
    <section
      className={`rounded-md border ${outerTone} px-2.5 py-2 space-y-1.5`}
      data-testid={baseId}
      data-impact-bucket={worstBucket}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-2 text-left"
        data-testid={`${baseId}-toggle`}
        aria-expanded={open}
      >
        <div className="flex items-center gap-1.5 text-[10.5px] font-semibold">
          <UserMinus className="h-3 w-3" />
          <span>
            {lang === 'en'
              ? 'Offensive injury impact'
              : 'Impacto ofensivo por lesiones'}
          </span>
          <TeamBucketPill
            teamName={home.team}
            bucket={homeBucket}
            count={home.missing_top5_count || 0}
            lang={lang}
            testId={`${baseId}-home-pill`}
          />
          <TeamBucketPill
            teamName={away.team}
            bucket={awayBucket}
            count={away.missing_top5_count || 0}
            lang={lang}
            testId={`${baseId}-away-pill`}
          />
        </div>
        <div className="flex items-center gap-2 text-[10px] opacity-85">
          {impact.under_support && (
            <Badge
              variant="outline"
              className="text-[8.5px] py-0 px-1 bg-cyan-500/10 border-cyan-500/40 text-cyan-200"
              data-testid={`${baseId}-under-support-pill`}
            >
              <TrendingDown className="h-2.5 w-2.5 mr-0.5" />
              {lang === 'en' ? 'Under support' : 'Apoyo al Under'}
            </Badge>
          )}
          {open ? <ChevronUp className="h-3 w-3 opacity-70" />
                : <ChevronDown className="h-3 w-3 opacity-70" />}
        </div>
      </button>

      {open && (
        <div
          className="space-y-2 pt-0.5"
          data-testid={`${baseId}-content`}
        >
          {/* Matchup narrative */}
          {impact.narrative_es && (
            <p
              className="text-[10px] leading-snug opacity-90 border-l-2 border-current/40 pl-2"
              data-testid={`${baseId}-narrative`}
            >
              {impact.narrative_es}
            </p>
          )}

          {/* Per-team detail blocks */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            <TeamBlock
              team={home}
              missing={homeMissing}
              lang={lang}
              testId={`${baseId}-home`}
            />
            <TeamBlock
              team={away}
              missing={awayMissing}
              lang={lang}
              testId={`${baseId}-away`}
            />
          </div>

          {/* Reason codes footer */}
          {Array.isArray(impact.reason_codes) && impact.reason_codes.length > 0 && (
            <div className="flex flex-wrap gap-1 pt-0.5">
              {impact.reason_codes
                .filter((rc) => rc && rc !== 'OFFENSIVE_INJURY_IMPACT_USED')
                .slice(0, 4)
                .map((rc) => (
                  <Badge
                    key={rc}
                    variant="outline"
                    className="text-[8.5px] py-0 px-1 opacity-75"
                    data-testid={`${baseId}-reason-${rc}`}
                  >
                    <Info className="h-2.5 w-2.5 mr-0.5" />
                    {rc.replaceAll('_', ' ').toLowerCase()}
                  </Badge>
                ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function TeamBucketPill({ teamName, bucket, count, lang, testId }) {
  const tone = BUCKET_TONE[bucket] || BUCKET_TONE.LOW;
  const dot  = BUCKET_DOT[bucket]  || BUCKET_DOT.LOW;
  const label = (BUCKET_LABELS[bucket] || {})[lang]
                || (BUCKET_LABELS[bucket] || {}).es
                || bucket;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border ${tone} px-1 py-[1px] text-[8.5px] font-mono`}
      data-testid={testId}
      data-bucket={bucket}
    >
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${dot}`} />
      <span className="opacity-90">{teamName || '—'}</span>
      <span className="opacity-75">·</span>
      <span>{label}</span>
      {count > 0 && (
        <>
          <span className="opacity-75">·</span>
          <span>{count}/5</span>
        </>
      )}
    </span>
  );
}

function TeamBlock({ team, missing, lang, testId }) {
  if (!team || !team.team) return null;
  const bucket = team.impact_bucket || 'LOW';
  const runsLost = team.run_creation_lost_estimate;
  const hasRunsLost = runsLost != null && Number(runsLost) > 0;
  return (
    <div
      className="rounded border border-border bg-card/40 px-2 py-1.5 space-y-1"
      data-testid={testId}
      data-team={team.team}
      data-bucket={bucket}
    >
      <div className="flex items-center justify-between text-[10.5px] font-semibold">
        <span className="flex items-center gap-1 truncate">
          <Activity className="h-3 w-3 opacity-70" />
          {team.team}
        </span>
        <span className="font-mono opacity-80">
          {team.offensive_injury_score != null
            ? `${team.offensive_injury_score}/100`
            : '—'}
        </span>
      </div>

      {missing.length === 0 ? (
        <p
          className="text-[10px] opacity-70"
          data-testid={`${testId}-empty`}
        >
          {lang === 'en'
            ? 'Top-5 hitters fully available.'
            : 'Top-5 bates disponibles.'}
        </p>
      ) : (
        <>
          <p
            className="text-[9.5px] uppercase tracking-wide opacity-60"
            data-testid={`${testId}-missing-label`}
          >
            {lang === 'en'
              ? `${missing.length} top-5 bat${missing.length > 1 ? 's' : ''} out`
              : `${missing.length} bate${missing.length > 1 ? 's' : ''} del top-5 fuera`}
          </p>
          <ul
            className="space-y-0.5"
            data-testid={`${testId}-missing-list`}
          >
            {missing.map((p) => (
              <li
                key={p.id || p.name}
                className="flex items-center justify-between gap-2 text-[10px]"
                data-testid={`${testId}-missing-${(p.id || p.name || '').toString().replaceAll(' ', '-').toLowerCase()}`}
              >
                <span className="truncate">
                  <span className="font-medium">{p.name}</span>
                  {p.position && (
                    <span className="opacity-60 ml-1">· {p.position}</span>
                  )}
                </span>
                <span className="font-mono opacity-80 text-[9.5px] whitespace-nowrap">
                  {fmtOps(p.ops) && (
                    <span data-testid={`${testId}-ops-${(p.id || p.name)}`}>
                      OPS {fmtOps(p.ops)}
                    </span>
                  )}
                  {p.hr != null && (
                    <span className="ml-1.5">
                      HR {fmtNum(p.hr)}
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      {hasRunsLost && (
        <div
          className="text-[10px] opacity-90 border-t border-current/15 pt-1 mt-1 flex items-center justify-between"
          data-testid={`${testId}-runs-lost`}
        >
          <span className="opacity-70">
            {lang === 'en' ? 'Run creation lost' : 'Producción perdida'}
          </span>
          <span className="font-mono">
            ≈ {fmtNum(runsLost, 2)}{' '}
            <span className="opacity-60">
              {lang === 'en' ? 'r/game' : 'c/juego'}
            </span>
          </span>
        </div>
      )}
    </div>
  );
}

export default OffensiveInjuryImpactPanel;
