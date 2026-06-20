"""Corner Momentum Study — Sprint C2 · PASO C2.2 + C2.3

Une el dataset base (football-data.co.uk, 4338 partidos con HC/AC) con
los datos ricos de Understat (xG, xGA, npxG, deep, PPDA, forecast).

Estrategia de matching:
  1) **Normalización**: lower-case, sin acentos ni puntuación.
  2) **Alias canónicos**: tabla manual para los 20-30 nombres conocidos
     que difieren entre las dos fuentes (ej. "Man United" vs
     "Manchester United", "Dortmund" vs "Borussia Dortmund", etc.).
  3) **Match key**: (date, league_code, normalized_home, normalized_away).
  4) Si una clave no matchea, intentamos resolverla con el alias map.

Salida:
  /app/data/corners_history/all_leagues_enriched_dataset.json
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

FD_DATASET = Path("/app/data/corners_history/all_leagues_dataset.json")
UND_DATASET = Path("/app/data/corners_history/understat_matches_consolidated.json")
OUT = Path("/app/data/corners_history/all_leagues_enriched_dataset.json")


# Alias canónicos: clave = nombre football-data, valor = nombre Understat
TEAM_ALIASES_FD_TO_UN = {
    # EPL
    "Man United":            "Manchester United",
    "Man City":              "Manchester City",
    "Newcastle":             "Newcastle United",
    "Tottenham":             "Tottenham",  # idem
    "Leeds":                 "Leeds",      # idem
    "Wolves":                "Wolverhampton Wanderers",
    "Sheffield United":      "Sheffield United",
    "Nott'm Forest":         "Nottingham Forest",
    "Nottingham":            "Nottingham Forest",
    "Leicester":             "Leicester",  # idem
    "West Brom":             "West Bromwich Albion",
    "West Ham":              "West Ham",
    "Brighton":              "Brighton",   # idem
    "Norwich":               "Norwich",    # idem
    "Watford":               "Watford",
    # Bundesliga
    "Dortmund":              "Borussia Dortmund",
    "Bayern Munich":         "Bayern Munich",
    "M'gladbach":            "Borussia M.Gladbach",
    "Ein Frankfurt":         "Eintracht Frankfurt",
    "Leverkusen":            "Bayer Leverkusen",
    "RB Leipzig":            "RasenBallsport Leipzig",
    "FC Koln":               "FC Cologne",
    "Stuttgart":             "VfB Stuttgart",
    "Mainz":                 "Mainz 05",
    "Union Berlin":          "Union Berlin",
    "Hertha":                "Hertha Berlin",
    "Greuther Furth":        "Greuther Fuerth",
    "Werder Bremen":         "Werder Bremen",
    "Schalke 04":            "Schalke 04",
    "Heidenheim":            "FC Heidenheim",
    "Bochum":                "Bochum",
    "Hoffenheim":            "Hoffenheim",
    "Augsburg":              "Augsburg",
    "Freiburg":              "Freiburg",
    "Darmstadt":             "Darmstadt",
    "Wolfsburg":             "Wolfsburg",
    # La Liga
    "Ath Bilbao":            "Athletic Club",
    "Ath Madrid":            "Atletico Madrid",
    "Betis":                 "Real Betis",
    "Sociedad":              "Real Sociedad",
    "Valladolid":            "Real Valladolid",
    "Cadiz":                 "Cadiz",
    "Vallecano":             "Rayo Vallecano",
    "Espanol":               "Espanyol",
    "Almeria":               "Almeria",
    "Las Palmas":            "Las Palmas",
    "Celta":                 "Celta Vigo",
    "Alaves":                "Alaves",
    "Mallorca":              "Mallorca",
    "Granada":               "Granada",
    "Levante":               "Levante",
    # Serie A
    "Verona":                "Verona",
    "AC Milan":              "AC Milan",
    "Hellas Verona":         "Verona",
    "Spezia":                "Spezia",
    "Cremonese":             "Cremonese",
    "Sampdoria":             "Sampdoria",
    "Salernitana":           "Salernitana",
    "Frosinone":             "Frosinone",
    # equipos con nombre idéntico no necesitan entrada, pero las dejamos
    # explícitas para que el normalizador no tropiece.
}


def _normalize(s: str) -> str:
    """lowercase + sin acentos + sin puntuación + sin espacios extra."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _canonical_fd_name(name: str) -> str:
    return TEAM_ALIASES_FD_TO_UN.get(name.strip(), name.strip())


def _fuzzy_match_pool(
    target_norm: str,
    candidates_norm: list[str],
    min_overlap: int = 4,
) -> str | None:
    """Para nombres no cubiertos por alias: busca el candidato con mayor
    overlap de tokens (al menos `min_overlap` chars compartidos).
    Conservador: prefiere matches donde uno contenga al otro.
    """
    if not target_norm:
        return None
    for c in candidates_norm:
        if c == target_norm:
            return c
    # substring (one contains the other)
    for c in candidates_norm:
        if target_norm in c or c in target_norm:
            # Evita matches espurios cortos
            if min(len(target_norm), len(c)) >= min_overlap:
                return c
    return None


def main() -> int:
    fd = json.loads(FD_DATASET.read_text(encoding="utf-8"))
    un = json.loads(UND_DATASET.read_text(encoding="utf-8"))
    print(f"[load] fd matches: {len(fd)} | un matches: {len(un)}")

    # Index understat by (date, league_code) → list of matches
    un_by_date_league: dict[tuple, list[dict]] = {}
    for r in un:
        key = (r["date"], r["league_code"])
        un_by_date_league.setdefault(key, []).append(r)

    # Para fuzzy: index understat también con (date, league_code) → set of normalized names
    un_norms_by_dlk: dict[tuple, dict[str, dict]] = {}
    for r in un:
        key = (r["date"], r["league_code"])
        un_norms_by_dlk.setdefault(key, {})
        un_norms_by_dlk[key][_normalize(r["home_team"])] = r

    matched, missed, ambiguous = 0, 0, 0
    enriched: list[dict] = []
    missed_examples: list[dict] = []
    alias_used: dict[str, int] = {}

    for fd_rec in fd:
        d, lc = fd_rec["date"], fd_rec["league_code"]
        fd_home_raw = fd_rec["home_team"]
        fd_away_raw = fd_rec["away_team"]
        # Apply alias map
        fd_home_canon = _canonical_fd_name(fd_home_raw)
        if fd_home_canon != fd_home_raw:
            alias_used[fd_home_raw] = alias_used.get(fd_home_raw, 0) + 1
        target_norm = _normalize(fd_home_canon)
        candidates = un_norms_by_dlk.get((d, lc), {})

        un_rec = candidates.get(target_norm)
        if un_rec is None:
            # try fuzzy on the list of candidate normalized names
            best = _fuzzy_match_pool(target_norm, list(candidates.keys()))
            if best is not None:
                un_rec = candidates[best]

        new_rec = dict(fd_rec)  # copy fd record
        if un_rec is not None:
            matched += 1
            # Validar que el away también matchea para evitar falsos positivos
            un_away_norm = _normalize(un_rec.get("away_team", ""))
            fd_away_norm = _normalize(_canonical_fd_name(fd_away_raw))
            if (un_away_norm == fd_away_norm
                or un_away_norm in fd_away_norm
                or fd_away_norm in un_away_norm):
                # válido
                pass
            else:
                ambiguous += 1
                # Mantener match pero anotar warning
                new_rec["_match_warning"] = (
                    f"away mismatch: fd={fd_away_raw} / un={un_rec.get('away_team')}")

            # añadir features ricas
            new_rec.update({
                "xg_h":              un_rec.get("xg_h"),
                "xg_a":              un_rec.get("xg_a"),
                "npxg_h":            un_rec.get("npxg_h"),
                "npxg_a":            un_rec.get("npxg_a"),
                "xga_h":             un_rec.get("xga_h"),
                "xga_a":             un_rec.get("xga_a"),
                "npxga_h":           un_rec.get("npxga_h"),
                "npxga_a":           un_rec.get("npxga_a"),
                "deep_h":            un_rec.get("deep_h"),
                "deep_a":            un_rec.get("deep_a"),
                "deep_allowed_h":    un_rec.get("deep_allowed_h"),
                "deep_allowed_a":    un_rec.get("deep_allowed_a"),
                "ppda_h":            un_rec.get("ppda_h"),
                "ppda_a":            un_rec.get("ppda_a"),
                "ppda_allowed_h":    un_rec.get("ppda_allowed_h"),
                "ppda_allowed_a":    un_rec.get("ppda_allowed_a"),
                "xpts_h":            un_rec.get("xpts_h"),
                "xpts_a":            un_rec.get("xpts_a"),
                "forecast_h_und":    un_rec.get("forecast_h"),
                "forecast_d_und":    un_rec.get("forecast_d"),
                "forecast_a_und":    un_rec.get("forecast_a"),
            })
        else:
            missed += 1
            if len(missed_examples) < 20:
                missed_examples.append({
                    "date": d, "league_code": lc,
                    "fd_home": fd_home_raw, "fd_home_canon": fd_home_canon,
                    "fd_away": fd_away_raw,
                    "candidates_on_date":
                        [c.get("home_team") for c in un_by_date_league.get((d, lc), [])],
                })
        enriched.append(new_rec)

    print(f"\n[matching] matched={matched} ({100*matched/len(fd):.1f}%)  "
           f"missed={missed}  ambiguous_away={ambiguous}")
    print(f"[aliases] applied: {len(alias_used)} distinct names → "
           f"{sum(alias_used.values())} matches")
    for alias, count in sorted(alias_used.items(), key=lambda x: -x[1])[:10]:
        print(f"   {alias!r}: {count}")

    if missed_examples:
        print(f"\n[missed-sample] first {len(missed_examples)} matches with no Understat counterpart:")
        for ex in missed_examples[:10]:
            print(f"   {ex['date']} {ex['league_code']} fd={ex['fd_home']!r} "
                   f"canon={ex['fd_home_canon']!r}")
            print(f"     candidates: {ex['candidates_on_date']}")

    # Cobertura final
    n = len(enriched)
    print(f"\n[coverage] non-null xg_h in enriched: "
           f"{sum(1 for r in enriched if r.get('xg_h') is not None)}/{n}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(enriched, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    print(f"\n[write] {OUT}  ({n} matches)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
