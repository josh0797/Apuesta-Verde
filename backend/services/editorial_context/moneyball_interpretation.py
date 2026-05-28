"""Moneyball interpretation of editorial signals.

The rules the engine MUST follow when looking at an editorial context block
attached to a match (Section 9 of the P3 spec):

  1. If editorial recommends a market AND moneyball confirms positive edge
     for the same market → slight confidence boost (and a copilot note).
  2. If editorial recommends a market AND moneyball verdict is NO_BET_VALUE
     or MARKET_TRAP → PUBLIC_NARRATIVE_RISK trap signal (anti-public bias).
  3. If multiple sources agree on factual context (e.g., motivation) AND
     moneyball verdict is consistent → contextual confidence boost.
  4. If editorial CONTRADICTS structured stats (e.g., calls a side "claro
     favorito" but their form_score is -50) → WARNING.

IMPORTANT: this module is **read-only**. It does NOT promote picks. It only
attaches an interpretation payload that the analyst engine and UI can
render. The final routing decision still belongs to moneyball_layer.
"""
from __future__ import annotations

from typing import Optional


def _norm_market(s: Optional[str]) -> str:
    return (s or "").strip().lower().replace("ó", "o").replace("é", "e")


def _markets_match(editorial_market: Optional[str], moneyball_market: Optional[str]) -> bool:
    if not editorial_market or not moneyball_market:
        return False
    a = _norm_market(editorial_market)
    b = _norm_market(moneyball_market)
    if a == b:
        return True
    # Loose match on common families
    families = [
        ("under", "under"),
        ("menos de", "under"),
        ("over", "over"),
        ("mas de", "over"),
        ("más de", "over"),
        ("doble oportunidad", "doble oportunidad"),
        ("no pierde", "doble oportunidad"),
        ("btts", "btts"),
        ("ambos equipos marcan", "btts"),
    ]
    fam_a = next((tag for kw, tag in families if kw in a), None)
    fam_b = next((tag for kw, tag in families if kw in b), None)
    return bool(fam_a and fam_a == fam_b)


def interpret(
    *,
    editorial: dict,
    moneyball_pick: Optional[dict] = None,
    moneyball_classification: Optional[str] = None,
) -> dict:
    """Build the 'How Moneyball interprets editorial context' payload.

    Inputs:
        editorial — the consensus dict returned by editorial_normalizer.build_consensus
        moneyball_pick — the engine pick (may be None when no value was found)
        moneyball_classification — e.g. 'VALUE_BET' / 'NO_BET_VALUE' / ...

    Output:
        {
            "editorial_available":  bool,
            "alignment":            "AGREES"|"DISAGREES"|"NO_MARKET"|"NO_PICK",
            "flags":                list[str],     # e.g. ['PUBLIC_NARRATIVE_RISK']
            "confidence_modifier":  int,           # -10..+8 (advice for UI only)
            "narrative":            str,           # one-paragraph ES explanation
            "factual_alignment":    list[str],     # which factual notes back the pick
        }
    """
    if not editorial or not editorial.get("available"):
        return {
            "editorial_available":  False,
            "alignment":            "NO_EDITORIAL",
            "flags":                [],
            "confidence_modifier":  0,
            "narrative":            "Sin contexto editorial disponible — el motor opera únicamente sobre datos estructurados.",
            "factual_alignment":    [],
        }

    ed_market = editorial.get("consensus_market")
    mb_market = (moneyball_pick or {}).get("market") or \
                ((moneyball_pick or {}).get("recommendation") or {}).get("market")
    classification = (moneyball_classification or "").upper()
    bias = editorial.get("narrative_bias_score") or 0
    contradictions = editorial.get("contradiction_flags") or []

    flags: list[str] = []
    modifier = 0
    alignment: str
    narrative: str

    if not moneyball_pick:
        # Engine didn't pick anything. Editorial recommends something → caution.
        alignment = "NO_PICK"
        if ed_market:
            flags.append("PUBLIC_NARRATIVE_RISK")
            narrative = (
                f"Las redacciones favorecen {ed_market!s} pero el motor no encontró "
                f"valor estructurado para este partido. Apostar siguiendo solo la "
                f"recomendación editorial implicaría asumir riesgo de narrativa pública."
            )
        else:
            narrative = (
                "Sin pick del motor y sin mercado consenso en las redacciones — "
                "el contexto editorial sólo aporta lectura humana, no señal de mercado."
            )
    elif _markets_match(ed_market, mb_market):
        # Aligned: editorial agrees with the moneyball pick.
        alignment = "AGREES"
        if classification in ("NO_BET_VALUE", "MARKET_TRAP", "FRAGILE_EDGE"):
            # Even if they agree, moneyball already decided no value → weight is low.
            flags.append("PUBLIC_NARRATIVE_RISK")
            narrative = (
                f"Las redacciones favorecen {ed_market!s}, igual que el mercado señala. "
                f"Sin embargo, el motor clasificó este pick como {classification.replace('_', ' ').lower()}, "
                f"así que no se recomienda apostar únicamente por el consenso narrativo."
            )
        else:
            # Real alignment. Tiny confidence bump.
            modifier = 5 if bias <= 30 else 2
            narrative = (
                f"El contexto editorial respalda el pick del motor ({mb_market!s}). "
                f"Los argumentos factuales coinciden, por lo que la confianza sube "
                f"+{modifier} puntos. El edge sigue siendo la condición necesaria."
            )
    elif ed_market and mb_market:
        # Editorial recommends something different from the engine.
        alignment = "DISAGREES"
        flags.append("PUBLIC_NARRATIVE_RISK")
        modifier = -3
        narrative = (
            f"Las redacciones favorecen {ed_market!s} mientras que el motor recomienda "
            f"{mb_market!s}. Discrepancia clara: el motor mantiene su lectura porque su "
            f"decisión se basa en edge medible, no en consenso narrativo."
        )
    else:
        # Editorial has no clear market but pick exists → use motivation notes.
        alignment = "NO_MARKET"
        narrative = (
            f"El motor recomienda {mb_market!s}. Las redacciones aportan contexto "
            f"motivacional/factual pero no sugieren un mercado claro — se usa solo "
            f"como soporte humano, no como señal de mercado."
        )

    # Bias penalty: if editorial is hyped, dial down the modifier.
    if bias >= 70:
        flags.append("HIGH_NARRATIVE_BIAS")
        modifier = min(modifier, -1)

    if "score_disagreement" in contradictions:
        flags.append("EDITORIAL_SCORE_DISAGREEMENT")
    if "market_disagreement" in contradictions:
        flags.append("EDITORIAL_MARKET_DISAGREEMENT")

    # Factual alignment list (the bullets that genuinely back the pick).
    factual_alignment = (editorial.get("factual_notes") or [])[:3]

    return {
        "editorial_available":  True,
        "alignment":            alignment,
        "flags":                flags,
        "confidence_modifier":  int(modifier),
        "narrative":            narrative,
        "factual_alignment":    factual_alignment,
    }


__all__ = ["interpret"]
