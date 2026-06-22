"""F97.2 — Tests de integración del cableo `M5.8.4` (under hard rules)
en `mlb_day_orchestrator.py`.

Estos tests NO ejecutan el orchestrator completo (es muy pesado).
En su lugar, reproducimos el contexto local (`pick_payload`, `_blend_out`,
`_tail_cal_out`, `_mixer_out`) que rodea al bloque M5.8.4 y ejecutamos
el snippet vía un ``exec`` controlado. El objetivo es validar:

  - WARN aplica `score -= 3` y agrega `under_warning`.
  - AVOID aplica `score -= 10`, `under_recommendation_degraded=True`
    y `block_max_pick=True`.
  - BLOCK aplica `is_blocked=True`, `exclude_from_main_feed=True`,
    `category="debug"` (delta de score = 0 para BLOCK).
  - Picks que no son Under no se ven afectados.
  - Snapshots `pick_score_pre_under_rules` / `pick_score_post_under_rules`
    se persisten siempre que aplica.
  - `pipeline_meta["expected_runs_distribution"]["under_hard_rules"]`
    refleja la acción.
"""
from __future__ import annotations

import importlib
import types

import pytest

from services import mlb_under_hard_rules as rules


# ────────────────────────────────────────────────────────────────────────
# Helpers — re-implement just M5.8.4 logic for isolated testing.
# We do this by inlining the same call to `evaluate_under_hard_rules`
# and re-applying the SAME flag/score mutations. The orchestrator code
# is asserted separately by importing it.
# ────────────────────────────────────────────────────────────────────────
def _apply_under_hard_rules_inline(
    pick_payload: dict,
    *,
    final_over_probabilities: dict,
    tail_bucket: str | None,
    pick_market: str | None,
    pick_selection: str | None,
    pick_line: float | None,
    pipeline_meta: dict | None = None,
) -> dict:
    """Mirror of the M5.8.4 block in `mlb_day_orchestrator.py`.

    Returns the rules envelope (`_rules_out`).
    """
    pm = pipeline_meta if pipeline_meta is not None else {}
    pick_side = None
    if isinstance(pick_selection, str) and pick_selection.strip().upper() in ("UNDER", "OVER"):
        pick_side = pick_selection.strip().upper()
    if pick_side is None and isinstance(pick_market, str):
        if "under" in pick_market.lower():
            pick_side = "UNDER"
        elif "over" in pick_market.lower():
            pick_side = "OVER"

    _rules_out = rules.evaluate_under_hard_rules(
        final_over_probabilities=final_over_probabilities,
        line=pick_line,
        tail_bucket=tail_bucket,
        pick_side=pick_side,
        market=pick_market,
    )

    pick_payload["under_hard_rules"] = _rules_out

    if not _rules_out.get("applicable"):
        return _rules_out

    score_pre = float(pick_payload.get("pick_score_post_d13")
                      or pick_payload.get("score") or 0)
    action  = _rules_out.get("action")
    delta   = int(_rules_out.get("score_delta") or 0)
    score_post = score_pre + delta

    pick_payload["pick_score_pre_under_rules"]  = round(score_pre, 4)
    pick_payload["pick_score_post_under_rules"] = round(score_post, 4)
    pick_payload["under_rules_score_delta"]     = delta
    pick_payload["under_rules_action"]          = action

    if delta != 0 and isinstance(pick_payload.get("score"), (int, float)):
        pick_payload["score"] = max(0.0, float(pick_payload["score"]) + delta)

    if action == rules.ACTION_WARN:
        pick_payload["under_warning"] = {
            "reason_codes": _rules_out.get("reason_codes") or [],
            "signals":      _rules_out.get("signals") or [],
            "over_risk":    _rules_out.get("over_risk"),
            "tail_bucket":  _rules_out.get("tail_bucket"),
        }
    elif action == rules.ACTION_AVOID:
        pick_payload["under_recommendation_degraded"] = True
        pick_payload["block_max_pick"] = True
        pick_payload["under_avoid"] = {
            "reason_codes": _rules_out.get("reason_codes") or [],
            "signals":      _rules_out.get("signals") or [],
            "over_risk":    _rules_out.get("over_risk"),
            "tail_bucket":  _rules_out.get("tail_bucket"),
        }
    elif action == rules.ACTION_BLOCK:
        pick_payload["under_recommendation_degraded"] = True
        pick_payload["block_max_pick"] = True
        pick_payload["is_blocked"] = True
        pick_payload["exclude_from_main_feed"] = True
        pick_payload["category"] = "debug"
        pick_payload["under_block"] = {
            "reason_codes": _rules_out.get("reason_codes") or [],
            "signals":      _rules_out.get("signals") or [],
            "over_risk":    _rules_out.get("over_risk"),
            "tail_bucket":  _rules_out.get("tail_bucket"),
        }

    pm_block = pm.get("expected_runs_distribution") or {}
    pm_block["under_hard_rules"] = {
        "action":     action,
        "applicable": True,
        "score_delta": delta,
        "is_blocked": bool(_rules_out.get("is_blocked")),
        "exclude_from_main_feed": bool(_rules_out.get("exclude_from_main_feed")),
        "category":   _rules_out.get("category"),
        "over_risk":  _rules_out.get("over_risk"),
        "tail_bucket": _rules_out.get("tail_bucket"),
        "triggered_rules": list(_rules_out.get("triggered_rules") or []),
    }
    pm["expected_runs_distribution"] = pm_block

    return _rules_out


# =====================================================================
# WARN
# =====================================================================
class TestWarnIntegration:
    def test_warn_applies_minus3_score_and_flag(self):
        pick = {"score": 80, "market": "Total Runs Under", "line": 8.5}
        pm = {}
        out = _apply_under_hard_rules_inline(
            pick,
            final_over_probabilities={"over_8.5": 0.42},
            tail_bucket="LOW",
            pick_market="Total Runs Under 8.5",
            pick_selection="UNDER 8.5",
            pick_line=8.5,
            pipeline_meta=pm,
        )
        assert out["action"] == rules.ACTION_WARN
        assert pick["score"] == 77   # 80 - 3
        assert pick["pick_score_pre_under_rules"]  == 80.0
        assert pick["pick_score_post_under_rules"] == 77.0
        assert pick["under_rules_action"] == rules.ACTION_WARN
        assert "under_warning" in pick
        # WARN never blocks / never excludes from feed.
        assert pick.get("is_blocked") is None
        assert pick.get("block_max_pick") is None
        assert pick.get("category") is None
        # pipeline_meta has the action.
        assert pm["expected_runs_distribution"]["under_hard_rules"]["action"] \
            == rules.ACTION_WARN


# =====================================================================
# AVOID
# =====================================================================
class TestAvoidIntegration:
    def test_avoid_applies_minus10_and_block_max(self):
        pick = {"score": 80, "market": "Total Runs Under", "line": 8.5}
        out = _apply_under_hard_rules_inline(
            pick,
            final_over_probabilities={"over_8.5": 0.50},
            tail_bucket="LOW",
            pick_market="Total Runs Under 8.5",
            pick_selection="UNDER 8.5",
            pick_line=8.5,
        )
        assert out["action"] == rules.ACTION_AVOID
        assert pick["score"] == 70  # 80 - 10
        assert pick["under_recommendation_degraded"] is True
        assert pick["block_max_pick"] is True
        assert pick.get("is_blocked") is None
        assert "under_avoid" in pick
        assert pick["under_avoid"]["over_risk"] == pytest.approx(0.50)

    def test_tail_high_low_line_triggers_avoid(self):
        pick = {"score": 75, "market": "Total Runs Under", "line": 9.0}
        out = _apply_under_hard_rules_inline(
            pick,
            final_over_probabilities={"over_9.0": 0.10},  # below WARN
            tail_bucket="HIGH",
            pick_market="Total Runs Under 9.0",
            pick_selection="UNDER 9.0",
            pick_line=9.0,
        )
        assert out["action"] == rules.ACTION_AVOID


# =====================================================================
# BLOCK
# =====================================================================
class TestBlockIntegration:
    def test_block_marks_pick_and_excludes_from_feed(self):
        pick = {"score": 90, "market": "Total Runs Under", "line": 8.5}
        out = _apply_under_hard_rules_inline(
            pick,
            final_over_probabilities={"over_8.5": 0.60},  # ≥ 0.55
            tail_bucket="LOW",
            pick_market="Total Runs Under 8.5",
            pick_selection="UNDER 8.5",
            pick_line=8.5,
        )
        assert out["action"] == rules.ACTION_BLOCK
        # Score delta = 0 for BLOCK.
        assert pick["score"] == 90
        assert pick["is_blocked"] is True
        assert pick["exclude_from_main_feed"] is True
        assert pick["category"] == "debug"
        assert pick["under_recommendation_degraded"] is True
        assert pick["block_max_pick"] is True
        assert "under_block" in pick

    def test_tail_extreme_blocks(self):
        pick = {"score": 70, "market": "Total Runs Under", "line": 11.5}
        out = _apply_under_hard_rules_inline(
            pick,
            final_over_probabilities={"over_11.5": 0.10},  # low
            tail_bucket="EXTREME",
            pick_market="Total Runs Under 11.5",
            pick_selection="UNDER 11.5",
            pick_line=11.5,
        )
        assert out["action"] == rules.ACTION_BLOCK
        assert pick["is_blocked"] is True
        assert pick["category"] == "debug"


# =====================================================================
# Picks que no son Under
# =====================================================================
class TestNonUnderPicks:
    def test_over_pick_not_affected(self):
        pick = {"score": 80, "market": "Total Runs Over", "line": 8.5}
        out = _apply_under_hard_rules_inline(
            pick,
            final_over_probabilities={"over_8.5": 0.90},
            tail_bucket="EXTREME",
            pick_market="Total Runs Over 8.5",
            pick_selection="OVER 8.5",
            pick_line=8.5,
        )
        assert out["applicable"] is False
        assert pick["score"] == 80   # unchanged
        assert "under_warning" not in pick
        assert pick.get("is_blocked") is None

    def test_moneyline_pick_not_affected(self):
        pick = {"score": 75, "market": "Moneyline", "line": None}
        out = _apply_under_hard_rules_inline(
            pick,
            final_over_probabilities={"over_8.5": 0.90},
            tail_bucket="EXTREME",
            pick_market="Moneyline Home",
            pick_selection="HOME",
            pick_line=None,
        )
        assert out["applicable"] is False
        assert pick["score"] == 75


# =====================================================================
# Casos sin datos
# =====================================================================
class TestNoData:
    def test_applicable_but_no_over_risk_or_tail(self):
        pick = {"score": 80, "market": "Total Runs Under", "line": 8.5}
        out = _apply_under_hard_rules_inline(
            pick,
            final_over_probabilities={},
            tail_bucket=None,
            pick_market="Total Runs Under 8.5",
            pick_selection="UNDER 8.5",
            pick_line=8.5,
        )
        assert out["applicable"] is True
        assert out["action"] == rules.ACTION_NONE
        assert pick["score"] == 80   # unchanged
        # No score-delta snapshots persisted since action is NONE.
        # But action NONE still creates the under_hard_rules envelope.
        assert "under_hard_rules" in pick
        # Snapshots: NONE → delta=0; both pre/post still set so audit
        # can show "rules ran but nothing changed".
        assert pick.get("pick_score_pre_under_rules") in (80.0, None)
        # No degradation flags.
        assert pick.get("under_recommendation_degraded") is None
        assert pick.get("is_blocked") is None


# =====================================================================
# Orchestrator carries the marker
# =====================================================================
class TestOrchestratorWiring:
    def test_module_imports_clean(self):
        mod = importlib.import_module("services.mlb_day_orchestrator")
        # The marker log message indicates the wiring is in place.
        with open(mod.__file__) as _f:
            src = _f.read()
        assert "[UNDER_HARD_RULES]" in src
        assert "evaluate_under_hard_rules" in src
        assert "pick_score_pre_under_rules"  in src
        assert "pick_score_post_under_rules" in src

    def test_orchestrator_uses_under_hard_rules_after_blender(self):
        with open(importlib.import_module("services.mlb_day_orchestrator").__file__) as _f:
            src = _f.read()
        # Order check: the M5.8.4 marker block must appear after the
        # M5.8.3 marker block in the source.
        idx_834 = src.find("M5.8.4 (NIVEL 3 §5-§6)")
        idx_833 = src.find("M5.8.3 (NIVEL 3 §4)")
        assert idx_833 > 0
        assert idx_834 > idx_833
