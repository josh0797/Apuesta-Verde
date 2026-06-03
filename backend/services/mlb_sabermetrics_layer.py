"""MLB Sabermetrics Layer (Phase 9.6).

Adds **WAR / OPS / FIP** confirmation/risk layer on top of the existing
Statcast helpers. Aligned with the engine's Moneyball philosophy:

  * **Never forces aggressive picks.**
  * **Never recommends Over on OPS alone.**
  * **Never recommends Run Line on WAR alone.**
  * Acts strictly as **confirmation + risk + hidden value** layer.
  * **Fail-soft**: missing inputs → ``available=False`` with deltas=0.
  * MLB-only — football/basketball never touch this module.

Data sourcing (in order of preference):
  1. ``pick_payload["advanced_stats_snapshot"]`` (from
     ``mlb_statcast_adapter`` — already normalized).
  2. Generic batting/pitcher/team profile blocks (legacy paths).

We **NEVER** import pybaseball / Bright Data directly from this file.
The adapter is the single source.

Reason codes are canonical (see RC_* constants below). Adjustments are
capped by helper (±15 each). Final orchestrator applies the
``data_quality`` weighting (strong=60%, partial/thin=35%, missing=0%).

Schema of ``calculate_sabermetric_context`` output:

    {
      "sabermetrics": {
        "available": bool,
        "data_quality": "strong" | "partial" | "missing",
        "home": {
            "ops_profile": {...},
            "war_impact":  {...},
            "starting_pitcher_fip": {...},
        },
        "away": {
            "ops_profile": {...},
            "war_impact":  {...},
            "starting_pitcher_fip": {...},
        },
        "match_edges": {
            "ops_edge":  "home"|"away"|"neutral",
            "fip_edge":  "home"|"away"|"neutral",
            "war_edge":  "home"|"away"|"neutral",
            "overall_sabermetric_edge": "home"|"away"|"neutral",
        },
        "adjustments": {
            "pitcher_quality_adjustment": float,
            "total_runs_adjustment":      float,  # +Over, -Under
            "fragility_adjustment":       float,
            "script_survival_adjustment": float,
            "run_line_support_adjustment": float,
        },
        "reason_codes": [...],
        "summary": str,
      }
    }
"""

from __future__ import annotations

from typing import Any

# ─────────────────────────────────────────────────────────────────────
# Constants — tiers and reason codes
# ─────────────────────────────────────────────────────────────────────
# OPS tiers
ELITE_OPS_THRESHOLD    = 0.850
STRONG_OPS_THRESHOLD   = 0.780
AVERAGE_OPS_THRESHOLD  = 0.700

OPS_TIER_ELITE    = "ELITE_OPS"
OPS_TIER_STRONG   = "STRONG_OPS"
OPS_TIER_AVERAGE  = "AVERAGE_OPS"
OPS_TIER_WEAK     = "WEAK_OPS"
OPS_TIER_UNKNOWN  = "UNKNOWN_OPS"

# FIP tiers
ELITE_FIP_CEILING    = 3.20
STRONG_FIP_CEILING   = 3.80
AVERAGE_FIP_CEILING  = 4.30

FIP_TIER_ELITE    = "ELITE_FIP"
FIP_TIER_STRONG   = "STRONG_FIP"
FIP_TIER_AVERAGE  = "AVERAGE_FIP"
FIP_TIER_RISKY    = "RISKY_FIP"
FIP_TIER_UNKNOWN  = "UNKNOWN_FIP"

# Default FIP constant (used when no league-adjusted constant is supplied)
DEFAULT_FIP_CONSTANT = 3.10

# ERA vs FIP gap thresholds
ERA_FIP_GAP_OVERSTATE  = 0.6   # ERA way better than FIP → hidden risk
ERA_FIP_GAP_UNDERSTATE = 0.6   # FIP way better than ERA → hidden value

# WAR tiers (per-player)
ELITE_WAR_THRESHOLD         = 5.0
STRONG_WAR_THRESHOLD        = 3.0
POSITIVE_WAR_THRESHOLD      = 1.5
REPLACEMENT_WAR_THRESHOLD   = 1.0

WAR_TIER_ELITE       = "ELITE_WAR"
WAR_TIER_STRONG      = "STRONG_WAR"
WAR_TIER_POSITIVE    = "POSITIVE_WAR"
WAR_TIER_REPLACEMENT = "REPLACEMENT_LEVEL"
WAR_TIER_UNKNOWN     = "UNKNOWN_WAR"

# Reason codes — OPS
RC_OPS_ELITE                  = "ELITE_OPS_PROFILE"
RC_OPS_STRONG                 = "STRONG_OPS_PROFILE"
RC_OPS_LOW_WARNING            = "LOW_OPS_WARNING"
RC_OPS_SUPPORTS_RUN_CREATION  = "OPS_SUPPORTS_RUN_CREATION"
RC_OPS_POWER_ON_BASE_SUPPORT  = "OPS_POWER_ON_BASE_SUPPORT"

# Reason codes — FIP
RC_FIP_ELITE                       = "ELITE_FIP_PROFILE"
RC_FIP_STRONG                      = "STRONG_FIP_PROFILE"
RC_FIP_RISKY                       = "RISKY_FIP_PROFILE"
RC_ERA_OVERSTATES                  = "ERA_OVERSTATES_PITCHER_QUALITY"
RC_ERA_UNDERSTATES                 = "ERA_UNDERSTATES_PITCHER_QUALITY"
RC_FIP_SUPPORTS_UNDER              = "FIP_SUPPORTS_UNDER"
RC_FIP_WARNING_FOR_UNDER           = "FIP_WARNING_FOR_UNDER"
RC_FIP_SUPPORTS_RUN_PREVENTION     = "FIP_SUPPORTS_RUN_PREVENTION"

# Reason codes — WAR
RC_WAR_ELITE_PLAYER       = "ELITE_WAR_PLAYER"
RC_WAR_STRONG_CORE        = "STRONG_WAR_CORE"
RC_WAR_LOW_LINEUP         = "LOW_WAR_LINEUP"
RC_WAR_SUPPORTS_TEAM_EDGE = "WAR_SUPPORTS_TEAM_EDGE"
RC_WAR_SUPPORTS_RUN_LINE  = "WAR_SUPPORTS_RUN_LINE"
RC_WAR_LINEUP_WEAKNESS    = "WAR_LINEUP_WEAKNESS"
RC_WAR_MISSING_HIGH       = "MISSING_HIGH_WAR_PLAYER"

# Reason codes — combined / context
RC_SABER_NO_DATA               = "SABERMETRICS_DATA_MISSING"
RC_SABER_PARTIAL               = "SABERMETRICS_PARTIAL_DATA"
RC_SABER_BOTH_TEAMS_STRONG_OPS = "BOTH_TEAMS_STRONG_OPS"
RC_SABER_BOTH_TEAMS_WEAK_OPS   = "BOTH_TEAMS_WEAK_OPS"
RC_SABER_BOTH_PITCHERS_ELITE   = "BOTH_PITCHERS_ELITE_FIP"
RC_SABER_BOTH_PITCHERS_RISKY   = "BOTH_PITCHERS_RISKY_FIP"

# Caps
_HELPER_CAP_OPS = 12.0
_HELPER_CAP_FIP = 15.0
_HELPER_CAP_WAR = 10.0


# ─────────────────────────────────────────────────────────────────────
# Coercion helpers
# ─────────────────────────────────────────────────────────────────────
def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ─────────────────────────────────────────────────────────────────────
# OPS profile
# ─────────────────────────────────────────────────────────────────────
def calculate_ops_profile(profile: dict | None) -> dict:
    """Compute the OPS profile for a player / team batting block.

    Accepts any of the following shapes (fail-soft):
      * ``{"team_ops": 0.812, "team_xwoba": 0.330, ...}``  (team block)
      * ``{"ops": 0.875, "obp": 0.360, "slg": 0.515, ...}`` (player block)
      * legacy ``{"obp": ..., "slg": ...}`` — OPS computed as OBP+SLG.

    Returns ``{"available", "tier", "ops", "obp", "slg",
    "power_on_base_score", "offensive_quality_score", "reason_codes"}``.
    """
    if not isinstance(profile, dict):
        return _ops_unavailable()

    # Locate OPS
    ops = _f(profile.get("team_ops") or profile.get("ops"))
    obp = _f(profile.get("obp") or profile.get("on_base_pct"))
    slg = _f(profile.get("slg") or profile.get("slugging_pct"))
    if ops is None and obp is not None and slg is not None:
        ops = round(obp + slg, 3)
    # xwoba as quality booster (statcast)
    xwoba = _f(profile.get("team_xwoba") or profile.get("xwoba"))
    barrel = _f(profile.get("team_barrel_pct") or profile.get("barrel_pct"))
    hard_hit = _f(profile.get("team_hard_hit_pct") or profile.get("hard_hit_pct"))

    if ops is None:
        return _ops_unavailable()

    # Tier
    if ops >= ELITE_OPS_THRESHOLD:
        tier = OPS_TIER_ELITE
    elif ops >= STRONG_OPS_THRESHOLD:
        tier = OPS_TIER_STRONG
    elif ops >= AVERAGE_OPS_THRESHOLD:
        tier = OPS_TIER_AVERAGE
    else:
        tier = OPS_TIER_WEAK

    reasons: list[str] = []
    # Offensive quality score 0..100
    # Anchor: 0.700 OPS → 50; 0.900 OPS → 90; linear clamp.
    offensive_quality_score = max(0.0, min(100.0, 50.0 + (ops - 0.700) * 200.0))

    # Power+OnBase composite (slightly bumped if Statcast confirms)
    power_on_base_score = offensive_quality_score
    if xwoba is not None and xwoba >= 0.330:
        power_on_base_score = min(100.0, power_on_base_score + 5.0)
        reasons.append(RC_OPS_POWER_ON_BASE_SUPPORT)
    if barrel is not None and barrel >= 8.5 and ops >= STRONG_OPS_THRESHOLD:
        power_on_base_score = min(100.0, power_on_base_score + 3.0)
        if RC_OPS_POWER_ON_BASE_SUPPORT not in reasons:
            reasons.append(RC_OPS_POWER_ON_BASE_SUPPORT)

    if tier == OPS_TIER_ELITE:
        reasons.append(RC_OPS_ELITE)
        reasons.append(RC_OPS_SUPPORTS_RUN_CREATION)
    elif tier == OPS_TIER_STRONG:
        reasons.append(RC_OPS_STRONG)
        reasons.append(RC_OPS_SUPPORTS_RUN_CREATION)
    elif tier == OPS_TIER_WEAK:
        reasons.append(RC_OPS_LOW_WARNING)

    return {
        "available":                True,
        "tier":                     tier,
        "ops":                      ops,
        "obp":                      obp,
        "slg":                      slg,
        "xwoba":                    xwoba,
        "barrel_pct":               barrel,
        "hard_hit_pct":             hard_hit,
        "power_on_base_score":      round(power_on_base_score, 1),
        "offensive_quality_score":  round(offensive_quality_score, 1),
        "reason_codes":             reasons,
    }


def _ops_unavailable() -> dict:
    return {
        "available":               False,
        "tier":                    OPS_TIER_UNKNOWN,
        "ops":                     None,
        "obp":                     None,
        "slg":                     None,
        "xwoba":                   None,
        "barrel_pct":              None,
        "hard_hit_pct":            None,
        "power_on_base_score":     0,
        "offensive_quality_score": 0,
        "reason_codes":            [],
    }


# ─────────────────────────────────────────────────────────────────────
# FIP profile
# ─────────────────────────────────────────────────────────────────────
def calculate_fip_profile(profile: dict | None, *,
                          fip_constant: float = DEFAULT_FIP_CONSTANT) -> dict:
    """Compute FIP-based pitcher profile.

    Supports two paths:
      A) Pre-computed FIP supplied directly (``fip`` key).
      B) Raw HR/BB/K/IP fields → compute FIP via formula
         ``FIP = ((13*HR) + 3*(BB+HBP) - 2*K) / IP + FIP_CONSTANT``.
      C) Fallback: use xERA as a proxy for FIP when none of the above
         are present (xERA is conceptually close to FIP — both strip
         defense / luck).

    Returns ``{available, tier, fip, era, era_minus_fip_gap, ...,
    defense_independent_pitching_score, pitcher_true_quality_score,
    reason_codes}``.
    """
    if not isinstance(profile, dict):
        return _fip_unavailable()

    era  = _f(profile.get("era"))
    fip  = _f(profile.get("fip"))
    xera = _f(profile.get("xera"))

    raw_hr  = _f(profile.get("hr"))
    raw_bb  = _f(profile.get("bb"))
    raw_hbp = _f(profile.get("hbp")) or 0.0
    raw_k   = _f(profile.get("k") or profile.get("so"))
    raw_ip  = _f(profile.get("ip") or profile.get("innings_pitched"))

    # Path B: derive FIP if raw stats are present
    if fip is None and all(v is not None for v in (raw_hr, raw_bb, raw_k, raw_ip)) and raw_ip > 0:
        fip = (
            (13.0 * raw_hr + 3.0 * (raw_bb + raw_hbp) - 2.0 * raw_k) / raw_ip
            + fip_constant
        )
        fip = round(fip, 2)

    # Path C: xERA as proxy
    fip_source = "direct"
    if fip is None and xera is not None:
        fip = round(xera, 2)
        fip_source = "xera_proxy"

    if fip is None:
        return _fip_unavailable(era=era, xera=xera)

    # Tier
    if fip <= ELITE_FIP_CEILING:
        tier = FIP_TIER_ELITE
    elif fip <= STRONG_FIP_CEILING:
        tier = FIP_TIER_STRONG
    elif fip <= AVERAGE_FIP_CEILING:
        tier = FIP_TIER_AVERAGE
    else:
        tier = FIP_TIER_RISKY

    reasons: list[str] = []
    # Defense-independent score (0..100) — 3.00 FIP → ~90, 4.50 FIP → ~50
    dip_score = max(0.0, min(100.0, 100.0 - (fip - 2.50) * 22.0))

    # ERA vs FIP gap
    era_minus_fip = None
    if era is not None:
        era_minus_fip = round(era - fip, 2)
        if era_minus_fip <= -ERA_FIP_GAP_OVERSTATE:
            # ERA much LOWER than FIP → ERA overstates (lucky)
            reasons.append(RC_ERA_OVERSTATES)
        elif era_minus_fip >= ERA_FIP_GAP_UNDERSTATE:
            # ERA much HIGHER than FIP → ERA understates (unlucky / hidden value)
            reasons.append(RC_ERA_UNDERSTATES)

    if tier == FIP_TIER_ELITE:
        reasons.extend([RC_FIP_ELITE, RC_FIP_SUPPORTS_UNDER,
                        RC_FIP_SUPPORTS_RUN_PREVENTION])
    elif tier == FIP_TIER_STRONG:
        reasons.extend([RC_FIP_STRONG, RC_FIP_SUPPORTS_UNDER])
    elif tier == FIP_TIER_RISKY:
        reasons.extend([RC_FIP_RISKY, RC_FIP_WARNING_FOR_UNDER])

    # Combined "true quality" score blends DIP + ERA (lightly) + xwoba_allowed if any.
    xwoba_allowed = _f(profile.get("xwoba_allowed"))
    quality_score = dip_score
    if xwoba_allowed is not None:
        # Lower xwoba_allowed boosts; 0.300 → +5 ; 0.350 → -5
        quality_score = max(0.0, min(100.0, quality_score + (0.325 - xwoba_allowed) * 200.0))

    return {
        "available":                          True,
        "tier":                               tier,
        "fip":                                fip,
        "fip_source":                         fip_source,
        "fip_constant":                       fip_constant,
        "era":                                era,
        "xera":                               xera,
        "era_minus_fip_gap":                  era_minus_fip,
        "defense_independent_pitching_score": round(dip_score, 1),
        "pitcher_true_quality_score":         round(quality_score, 1),
        "reason_codes":                       reasons,
    }


def _fip_unavailable(era: float | None = None, xera: float | None = None) -> dict:
    return {
        "available":                          False,
        "tier":                               FIP_TIER_UNKNOWN,
        "fip":                                None,
        "fip_source":                         None,
        "fip_constant":                       DEFAULT_FIP_CONSTANT,
        "era":                                era,
        "xera":                               xera,
        "era_minus_fip_gap":                  None,
        "defense_independent_pitching_score": 0,
        "pitcher_true_quality_score":         0,
        "reason_codes":                       [],
    }


# ─────────────────────────────────────────────────────────────────────
# WAR impact
# ─────────────────────────────────────────────────────────────────────
def calculate_war_impact(player_profiles: dict | list | None,
                          lineup_context: dict | None = None) -> dict:
    """Aggregate WAR signals from player profiles and (optional) lineup ctx.

    Accepts:
      * ``list[dict]`` of player dicts with at least ``war`` key.
      * ``dict`` with ``hitters: [...], pitchers: [...], bullpen: [...]``
        and optional ``team_total_war``.
      * a single dict with ``team_war``/``team_total_war``.

    Fail-soft: missing WAR → ``available=False``.
    """
    hitters: list[dict] = []
    pitchers: list[dict] = []
    bullpen: list[dict] = []
    team_total_war: float | None = None

    if isinstance(player_profiles, list):
        hitters = [p for p in player_profiles if isinstance(p, dict)]
    elif isinstance(player_profiles, dict):
        team_total_war = _f(player_profiles.get("team_total_war")
                            or player_profiles.get("team_war"))
        hitters  = [p for p in (player_profiles.get("hitters") or []) if isinstance(p, dict)]
        pitchers = [p for p in (player_profiles.get("pitchers") or []) if isinstance(p, dict)]
        bullpen  = [p for p in (player_profiles.get("bullpen") or []) if isinstance(p, dict)]

    # Active vs inactive based on lineup_context (very simple — fail-soft).
    inactive_keys = set()
    if isinstance(lineup_context, dict):
        for p in (lineup_context.get("inactive") or []):
            if isinstance(p, dict):
                pid = p.get("id") or p.get("player_id") or p.get("name")
                if pid is not None:
                    inactive_keys.add(str(pid).lower())

    def _is_active(p: dict) -> bool:
        pid = p.get("id") or p.get("player_id") or p.get("name")
        if pid is None:
            return True
        return str(pid).lower() not in inactive_keys

    active_hitters = [h for h in hitters if _is_active(h)]
    war_values = [_f(h.get("war")) for h in active_hitters]
    war_values = [v for v in war_values if v is not None]

    # Pitcher (starter) WAR — first entry assumed to be SP if not flagged.
    starter_war = None
    for p in pitchers:
        if p.get("role") in (None, "starter", "SP") and _f(p.get("war")) is not None:
            starter_war = _f(p.get("war"))
            break

    bullpen_war_total = sum(
        _f(r.get("war")) or 0.0 for r in bullpen
    ) if bullpen else None

    # Bail out when no WAR signal at all
    if not war_values and starter_war is None and team_total_war is None:
        return _war_unavailable()

    reasons: list[str] = []

    # Lineup score: average of top 4 hitter WAR (anchor ELITE 5.0 → 90)
    if war_values:
        top4 = sorted(war_values, reverse=True)[:4]
        avg_top4 = sum(top4) / len(top4)
    else:
        avg_top4 = None

    elite_count = sum(1 for v in war_values if v >= ELITE_WAR_THRESHOLD)
    strong_count = sum(1 for v in war_values if v >= STRONG_WAR_THRESHOLD)
    replacement_count = sum(
        1 for v in war_values if v < REPLACEMENT_WAR_THRESHOLD
    )

    if elite_count >= 1:
        reasons.append(RC_WAR_ELITE_PLAYER)
    if strong_count >= 2:
        reasons.append(RC_WAR_STRONG_CORE)
    if war_values and replacement_count >= len(war_values) * 0.5:
        reasons.append(RC_WAR_LOW_LINEUP)
        reasons.append(RC_WAR_LINEUP_WEAKNESS)
    # Missing high-WAR (someone with WAR>3 is inactive)
    missing_high = False
    if isinstance(lineup_context, dict):
        for p in (lineup_context.get("inactive") or []):
            if isinstance(p, dict) and (_f(p.get("war")) or 0) >= STRONG_WAR_THRESHOLD:
                missing_high = True
                break
    if missing_high:
        reasons.append(RC_WAR_MISSING_HIGH)

    # Lineup WAR score (0..100): avg_top4 5.0 → 90 ; 1.0 → 30
    if avg_top4 is not None:
        lineup_war_score = max(0.0, min(100.0, 30.0 + (avg_top4 - 1.0) * 15.0))
    else:
        lineup_war_score = 0.0

    # Pitcher WAR score
    if starter_war is not None:
        pitcher_war_score = max(0.0, min(100.0, 30.0 + starter_war * 12.0))
    else:
        pitcher_war_score = 0.0

    # Team impact composite — weighted lineup+pitcher
    team_impact_score = round(
        lineup_war_score * 0.6 + pitcher_war_score * 0.4, 1,
    )

    # Replacement gap — sum of (war - replacement_level) for active hitters
    replacement_gap_score = (
        sum(max(0.0, (v - REPLACEMENT_WAR_THRESHOLD)) for v in war_values)
        if war_values else 0.0
    )

    return {
        "available":               True,
        "team_total_war":          team_total_war,
        "lineup_war_score":        round(lineup_war_score, 1),
        "pitcher_war_score":       round(pitcher_war_score, 1),
        "team_impact_score":       team_impact_score,
        "replacement_gap_score":   round(replacement_gap_score, 2),
        "active_hitters_count":    len(active_hitters),
        "elite_war_count":         elite_count,
        "strong_war_count":        strong_count,
        "replacement_count":       replacement_count,
        "starter_war":             starter_war,
        "bullpen_war_total":       bullpen_war_total,
        "reason_codes":            reasons,
    }


def _war_unavailable() -> dict:
    return {
        "available":             False,
        "team_total_war":        None,
        "lineup_war_score":      0,
        "pitcher_war_score":     0,
        "team_impact_score":     0,
        "replacement_gap_score": 0,
        "active_hitters_count":  0,
        "elite_war_count":       0,
        "strong_war_count":      0,
        "replacement_count":     0,
        "starter_war":           None,
        "bullpen_war_total":     None,
        "reason_codes":          [],
    }


# ─────────────────────────────────────────────────────────────────────
# Combined match-level context
# ─────────────────────────────────────────────────────────────────────
def calculate_sabermetric_context(payload: dict | None) -> dict:
    """Build the full sabermetric context from a pick_payload or match_doc.

    Reads from ``advanced_stats_snapshot`` (preferred) and legacy
    batting/pitcher/lineup profile blocks. Pure function, fail-soft.
    """
    if not isinstance(payload, dict):
        return _saber_unavailable()

    snap = payload.get("advanced_stats_snapshot") or {}
    home_pa = snap.get("home_pitcher_advanced") or {}
    away_pa = snap.get("away_pitcher_advanced") or {}
    home_ta = snap.get("home_team_advanced") or {}
    away_ta = snap.get("away_team_advanced") or {}

    home_pitcher_raw = home_pa.get("pitcher") if isinstance(home_pa, dict) else {}
    away_pitcher_raw = away_pa.get("pitcher") if isinstance(away_pa, dict) else {}
    home_team_raw    = home_ta.get("team")    if isinstance(home_ta, dict) else {}
    away_team_raw    = away_ta.get("team")    if isinstance(away_ta, dict) else {}

    # Merge legacy profiles (lineup/batting/pitcher) — they may also carry WAR.
    home_team_extra = payload.get("home_team_profile") or {}
    away_team_extra = payload.get("away_team_profile") or {}

    home_ops = calculate_ops_profile({**(home_team_raw or {}), **(home_team_extra or {})})
    away_ops = calculate_ops_profile({**(away_team_raw or {}), **(away_team_extra or {})})

    home_fip = calculate_fip_profile(home_pitcher_raw or {})
    away_fip = calculate_fip_profile(away_pitcher_raw or {})

    home_war = calculate_war_impact(
        payload.get("home_lineup_profiles") or home_team_extra,
        lineup_context=payload.get("home_lineup_context"),
    )
    away_war = calculate_war_impact(
        payload.get("away_lineup_profiles") or away_team_extra,
        lineup_context=payload.get("away_lineup_context"),
    )

    # Data quality
    avail_count = sum(1 for b in (home_ops, away_ops, home_fip, away_fip,
                                    home_war, away_war) if b.get("available"))
    if avail_count == 0:
        dq = "missing"
    elif avail_count <= 2:
        dq = "thin"
    elif avail_count <= 4:
        dq = "partial"
    else:
        dq = "strong"

    if dq == "missing":
        out = _saber_unavailable()
        out["sabermetrics"]["home"] = {
            "ops_profile": home_ops, "war_impact": home_war,
            "starting_pitcher_fip": home_fip,
        }
        out["sabermetrics"]["away"] = {
            "ops_profile": away_ops, "war_impact": away_war,
            "starting_pitcher_fip": away_fip,
        }
        return out

    # ── Match edges ─────────────────────────────────────────────────
    ops_edge = _edge_sign(home_ops.get("ops"), away_ops.get("ops"), tol=0.030)
    fip_edge = _edge_sign(away_fip.get("fip"), home_fip.get("fip"), tol=0.30)  # LOWER fip wins
    war_edge = _edge_sign(home_war.get("team_impact_score"),
                           away_war.get("team_impact_score"), tol=8.0)

    overall_edge = _consensus_edge([ops_edge, fip_edge, war_edge])

    # ── Adjustments (conservative, capped) ──────────────────────────
    adjustments = _build_adjustments(home_ops, away_ops,
                                       home_fip, away_fip,
                                       home_war, away_war)

    # Reason-code union
    rcs: list[str] = []
    for src in (home_ops, away_ops, home_fip, away_fip, home_war, away_war):
        for rc in (src.get("reason_codes") or []):
            if rc not in rcs:
                rcs.append(rc)

    # Combined patterns
    if home_ops.get("tier") in (OPS_TIER_ELITE, OPS_TIER_STRONG) \
            and away_ops.get("tier") in (OPS_TIER_ELITE, OPS_TIER_STRONG):
        rcs.append(RC_SABER_BOTH_TEAMS_STRONG_OPS)
    if home_ops.get("tier") == OPS_TIER_WEAK and away_ops.get("tier") == OPS_TIER_WEAK:
        rcs.append(RC_SABER_BOTH_TEAMS_WEAK_OPS)
    if home_fip.get("tier") == FIP_TIER_ELITE and away_fip.get("tier") == FIP_TIER_ELITE:
        rcs.append(RC_SABER_BOTH_PITCHERS_ELITE)
    if home_fip.get("tier") == FIP_TIER_RISKY and away_fip.get("tier") == FIP_TIER_RISKY:
        rcs.append(RC_SABER_BOTH_PITCHERS_RISKY)
    if dq == "partial" or dq == "thin":
        rcs.append(RC_SABER_PARTIAL)

    summary = _build_summary(dq, ops_edge, fip_edge, war_edge,
                              overall_edge, adjustments)

    return {
        "sabermetrics": {
            "available":   True,
            "data_quality": dq,
            "home": {
                "ops_profile":          home_ops,
                "war_impact":           home_war,
                "starting_pitcher_fip": home_fip,
            },
            "away": {
                "ops_profile":          away_ops,
                "war_impact":           away_war,
                "starting_pitcher_fip": away_fip,
            },
            "match_edges": {
                "ops_edge":                  ops_edge,
                "fip_edge":                  fip_edge,
                "war_edge":                  war_edge,
                "overall_sabermetric_edge":  overall_edge,
            },
            "adjustments":  adjustments,
            "reason_codes": rcs,
            "summary":      summary,
        },
    }


def _saber_unavailable() -> dict:
    empty_ops = _ops_unavailable()
    empty_fip = _fip_unavailable()
    empty_war = _war_unavailable()
    return {
        "sabermetrics": {
            "available":   False,
            "data_quality": "missing",
            "home": {
                "ops_profile":          empty_ops,
                "war_impact":           empty_war,
                "starting_pitcher_fip": empty_fip,
            },
            "away": {
                "ops_profile":          empty_ops,
                "war_impact":           empty_war,
                "starting_pitcher_fip": empty_fip,
            },
            "match_edges": {
                "ops_edge":                  "neutral",
                "fip_edge":                  "neutral",
                "war_edge":                  "neutral",
                "overall_sabermetric_edge":  "neutral",
            },
            "adjustments": {
                "pitcher_quality_adjustment":  0,
                "total_runs_adjustment":       0,
                "fragility_adjustment":        0,
                "script_survival_adjustment":  0,
                "run_line_support_adjustment": 0,
            },
            "reason_codes": [RC_SABER_NO_DATA],
            "summary":      "Sabermetría no disponible (datos insuficientes).",
        },
    }


# ─────────────────────────────────────────────────────────────────────
# Internals — edges, adjustments, summary
# ─────────────────────────────────────────────────────────────────────
def _edge_sign(home_value: float | None,
                away_value: float | None,
                *, tol: float) -> str:
    """Return 'home'/'away'/'neutral'. Higher value = home edge."""
    hv = _f(home_value)
    av = _f(away_value)
    if hv is None or av is None:
        return "neutral"
    diff = hv - av
    if diff >= tol:
        return "home"
    if diff <= -tol:
        return "away"
    return "neutral"


def _consensus_edge(edges: list[str]) -> str:
    home = sum(1 for e in edges if e == "home")
    away = sum(1 for e in edges if e == "away")
    if home >= 2 and home > away:
        return "home"
    if away >= 2 and away > home:
        return "away"
    return "neutral"


def _build_adjustments(home_ops: dict, away_ops: dict,
                       home_fip: dict, away_fip: dict,
                       home_war: dict, away_war: dict) -> dict:
    """Compose conservative adjustments — capped per helper.

    Sign conventions:
      * total_runs_adjustment: + supports Over, - supports Under.
      * pitcher_quality_adjustment: + boosts pitcher confidence (both sides).
      * fragility_adjustment: + increases fragility.
      * script_survival_adjustment: + bonus to Under script survival.
      * run_line_support_adjustment: + supports favorite run line.
    """
    pq = 0.0
    runs = 0.0
    frag = 0.0
    surv = 0.0
    rl = 0.0

    # FIP impact (pitcher quality + survival)
    for fip in (home_fip, away_fip):
        if not fip.get("available"):
            continue
        if fip.get("tier") == FIP_TIER_ELITE:
            pq += 6.0
            surv += 4.0
        elif fip.get("tier") == FIP_TIER_STRONG:
            pq += 3.0
            surv += 2.0
        elif fip.get("tier") == FIP_TIER_RISKY:
            pq -= 5.0
            surv -= 4.0
            frag += 4.0
        # ERA vs FIP gap
        gap = fip.get("era_minus_fip_gap")
        if isinstance(gap, (int, float)):
            if gap <= -ERA_FIP_GAP_OVERSTATE:
                # ERA lower than FIP → lucky pitcher → fragility up
                frag += 3.0
                pq -= 2.0
            elif gap >= ERA_FIP_GAP_UNDERSTATE:
                # ERA higher than FIP → hidden value
                pq += 2.0

    # OPS impact (run creation + fragility for Under)
    for ops in (home_ops, away_ops):
        if not ops.get("available"):
            continue
        if ops.get("tier") == OPS_TIER_ELITE:
            runs += 4.0
            frag += 2.0
        elif ops.get("tier") == OPS_TIER_STRONG:
            runs += 2.0
        elif ops.get("tier") == OPS_TIER_WEAK:
            runs -= 3.0
            surv += 2.0

    # WAR impact (run line + team edge)
    h_imp = home_war.get("team_impact_score") if home_war.get("available") else None
    a_imp = away_war.get("team_impact_score") if away_war.get("available") else None
    if h_imp is not None and a_imp is not None:
        diff = h_imp - a_imp
        if abs(diff) >= 12:
            rl += 4.0 if diff > 0 else -4.0
        elif abs(diff) >= 6:
            rl += 2.0 if diff > 0 else -2.0

    # Caps
    pq   = _clamp(pq,   -_HELPER_CAP_FIP, _HELPER_CAP_FIP)
    runs = _clamp(runs, -_HELPER_CAP_OPS, _HELPER_CAP_OPS)
    frag = _clamp(frag, -10.0, 10.0)
    surv = _clamp(surv, -10.0, 10.0)
    rl   = _clamp(rl,   -_HELPER_CAP_WAR, _HELPER_CAP_WAR)

    return {
        "pitcher_quality_adjustment":  round(pq, 2),
        "total_runs_adjustment":       round(runs, 2),
        "fragility_adjustment":        round(frag, 2),
        "script_survival_adjustment":  round(surv, 2),
        "run_line_support_adjustment": round(rl, 2),
    }


def _build_summary(dq: str,
                    ops_edge: str, fip_edge: str, war_edge: str,
                    overall: str, adj: dict) -> str:
    parts: list[str] = [f"Sabermetría disponible ({dq})."]
    if overall != "neutral":
        parts.append(f"Ventaja sabermétrica general: {overall}.")
    if fip_edge != "neutral":
        parts.append(f"Ventaja FIP: {fip_edge}.")
    if ops_edge != "neutral":
        parts.append(f"Ventaja OPS: {ops_edge}.")
    if war_edge != "neutral":
        parts.append(f"Ventaja WAR: {war_edge}.")
    if adj.get("total_runs_adjustment", 0) < 0:
        parts.append("La capa apoya entornos Under.")
    elif adj.get("total_runs_adjustment", 0) > 0:
        parts.append("La capa apoya entornos Over (con confirmación adicional).")
    return " ".join(parts)


# ─────────────────────────────────────────────────────────────────────
# Public weighting helper (orchestrator contract)
# ─────────────────────────────────────────────────────────────────────
WEIGHT_BY_QUALITY = {"strong": 0.60, "partial": 0.35, "thin": 0.35, "missing": 0.0}


def derive_sabermetric_recommendation_delta(
    sabermetric_ctx: dict | None,
    *,
    pick_market: str | None = None,
) -> dict:
    """Translate the sabermetric context into a single, weighted set of
    deltas the orchestrator can apply.

    Returns ``{used, data_quality, weight, raw_conf_delta,
    weighted_conf_delta, raw_breakdown, reason_codes}``.

    Sign convention:
      * confidence_delta signed wrt the picked side (Under/Over).
      * caller decides whether to also apply fragility / survival deltas.
    """
    out = {
        "used":                False,
        "data_quality":        "missing",
        "weight":              0.0,
        "raw_conf_delta":      0.0,
        "weighted_conf_delta": 0.0,
        "raw_breakdown":       {},
        "reason_codes":        [],
    }
    if not isinstance(sabermetric_ctx, dict):
        return out
    inner = sabermetric_ctx.get("sabermetrics") if "sabermetrics" in sabermetric_ctx else sabermetric_ctx
    if not isinstance(inner, dict) or not inner.get("available"):
        return out

    dq = inner.get("data_quality") or "missing"
    weight = WEIGHT_BY_QUALITY.get(dq, 0.0)
    adj = inner.get("adjustments") or {}

    market = (pick_market or "").lower()
    is_under = "under" in market and "team total" not in market
    is_over  = "over"  in market and "team total" not in market

    # Translate total_runs adjustment relative to the chosen side.
    total_runs = float(adj.get("total_runs_adjustment") or 0)
    if is_under:
        side_runs_conf = -total_runs  # under → positive runs adj hurts
    elif is_over:
        side_runs_conf = total_runs
    else:
        side_runs_conf = 0.0

    pq = float(adj.get("pitcher_quality_adjustment") or 0)
    # Pitcher quality contribution is direction-aware:
    #   * Under pick: positive pq (good pitchers) supports Under.
    #   * Over pick:  negative pq (bad pitchers) supports Over.
    if is_under:
        pq_side = pq * 0.4
    elif is_over:
        pq_side = -pq * 0.4
    else:
        pq_side = 0.0

    surv = float(adj.get("script_survival_adjustment") or 0) if is_under else 0.0

    # Fragility: only hurts UNDER picks (Over thrives on fragility).
    if is_under:
        frag = -float(adj.get("fragility_adjustment") or 0) * 0.5
    else:
        frag = 0.0

    # Composite raw delta
    raw = side_runs_conf + pq_side + surv + frag

    weighted = round(raw * weight, 2)

    # **Guardrail**: sabermetrics alone must never push more than ±15.
    weighted = max(-15.0, min(15.0, weighted))

    out.update({
        "used":                True,
        "data_quality":        dq,
        "weight":              weight,
        "raw_conf_delta":      round(raw, 3),
        "weighted_conf_delta": weighted,
        "raw_breakdown": {
            "total_runs_adjustment":      total_runs,
            "pitcher_quality_adjustment": pq,
            "script_survival_adjustment": float(adj.get("script_survival_adjustment") or 0),
            "fragility_adjustment":       float(adj.get("fragility_adjustment") or 0),
            "run_line_support_adjustment": float(adj.get("run_line_support_adjustment") or 0),
        },
        "reason_codes":        list(inner.get("reason_codes") or []),
    })
    return out


__all__ = [
    # Tier constants
    "OPS_TIER_ELITE", "OPS_TIER_STRONG", "OPS_TIER_AVERAGE",
    "OPS_TIER_WEAK", "OPS_TIER_UNKNOWN",
    "FIP_TIER_ELITE", "FIP_TIER_STRONG", "FIP_TIER_AVERAGE",
    "FIP_TIER_RISKY", "FIP_TIER_UNKNOWN",
    "WAR_TIER_ELITE", "WAR_TIER_STRONG", "WAR_TIER_POSITIVE",
    "WAR_TIER_REPLACEMENT", "WAR_TIER_UNKNOWN",
    "DEFAULT_FIP_CONSTANT",
    # Reason codes (export the most-consumed)
    "RC_OPS_ELITE", "RC_OPS_STRONG", "RC_OPS_LOW_WARNING",
    "RC_OPS_SUPPORTS_RUN_CREATION", "RC_OPS_POWER_ON_BASE_SUPPORT",
    "RC_FIP_ELITE", "RC_FIP_STRONG", "RC_FIP_RISKY",
    "RC_ERA_OVERSTATES", "RC_ERA_UNDERSTATES",
    "RC_FIP_SUPPORTS_UNDER", "RC_FIP_WARNING_FOR_UNDER",
    "RC_FIP_SUPPORTS_RUN_PREVENTION",
    "RC_WAR_ELITE_PLAYER", "RC_WAR_STRONG_CORE",
    "RC_WAR_LOW_LINEUP", "RC_WAR_SUPPORTS_TEAM_EDGE",
    "RC_WAR_SUPPORTS_RUN_LINE", "RC_WAR_LINEUP_WEAKNESS",
    "RC_WAR_MISSING_HIGH",
    "RC_SABER_NO_DATA", "RC_SABER_PARTIAL",
    "RC_SABER_BOTH_TEAMS_STRONG_OPS", "RC_SABER_BOTH_TEAMS_WEAK_OPS",
    "RC_SABER_BOTH_PITCHERS_ELITE", "RC_SABER_BOTH_PITCHERS_RISKY",
    # Public API
    "calculate_ops_profile",
    "calculate_fip_profile",
    "calculate_war_impact",
    "calculate_sabermetric_context",
    "derive_sabermetric_recommendation_delta",
]
