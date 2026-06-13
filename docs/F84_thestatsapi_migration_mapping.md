# F84 — Mapeo de migración API-Sports → TheStatsAPI

> **Objetivo:** Invertir la prioridad de las fuentes para los bloques de
> enriquecimiento estructural (team_stats, h2h, odds) — TheStatsAPI pasa a
> ser **primaria**, API-Sports queda como **fallback** detrás de flag
> (`ENABLE_API_SPORTS_FALLBACK`).
>
> **Estrategia confirmada con usuario:**
> - Inversión de prioridad (no reemplazo total, no shadow mode).
> - API-Sports se mantiene como red de seguridad detrás de flag.
> - Migración end-to-end de **un bloque por vez**, con tests, antes de
>   pasar al siguiente.

---

## 1. Inventario de uso actual

### 1.1 `services/api_football.py` (API-Sports v3, sólo football)

| Función | Endpoint API-Sports | Consumidores | Bloque |
|---|---|---|---|
| `team_statistics(team_id, league_id, season)` | `GET /teams/statistics?team=X&league=Y&season=Z` | `data_ingestion.py:646-648` | **a) team_stats** |
| `head_to_head(home_id, away_id, limit=5)` | `GET /fixtures/headtohead?h2h=X-Y` | `data_ingestion.py:650`, `head_to_head_ingestor.py` | **b) h2h** |
| `odds(fixture_id)` | `GET /odds?fixture=X` | `data_ingestion.py` (odds enrichment) | **e) odds** |
| `standings(league_id, season)` | `GET /standings?league=Y&season=Z` | varios | (fuera de scope inicial) |
| `injuries(team_id, season)` | `GET /injuries?team=X&season=Z` | injury_intelligence | (fuera de scope inicial) |
| `fixture_statistics(fixture_id)` | `GET /fixtures/statistics?fixture=X` | corners stats | (cubierto por F82.2) |

### 1.2 `services/api_sports.py` (API-Sports v3, multi-sport)

- Espejo de `api_football.py` pero con dispatch por sport. Sólo se toca
  el camino football en F84 — basketball / baseball quedan intactos.

### 1.3 Lectores del shape canónico

| Consumidor | Campos críticos esperados |
|---|---|
| `football_data_enrichment_normalizer._normalize_team_stats_block` | `xg_for_avg`, `xg_against_avg`, `goals_for_avg`, `goals_against_avg`, `corners_for_avg`, `corners_against_avg`, `points`, `position`, `form`, `played` |
| `football_editorial_prediction._build_head_to_head` | lista de dicts con `date`, `home_team`, `away_team`, `score` |
| `football_data_enrichment.normalize_football_enrichment` | mismos campos team_stats + odds |

---

## 2. Cobertura de TheStatsAPI por bloque

Base URL: `https://api.thestatsapi.com/api` · Auth: `Authorization: Bearer YOUR_API_KEY`.

### 2.1 Bloque **a) team_stats / season_aggregates** — ✅ Cobertura DIRECTA

**Endpoint:** `GET /football/teams/{team_id}/stats?season_id=sn_xxx`

**Respuesta canónica:**
```json
{
  "data": {
    "team_id": "tm_8923",
    "season_id": "sn_7210",
    "competition_id": "comp_3879",
    "matches_played": 38,
    "wins": 23,
    "draws": 6,
    "losses": 9,
    "points": 75,
    "position": 3,
    "goals_for": 58,
    "goals_against": 43,
    "goal_difference": 15,
    "form": "WWDLW"
  }
}
```

**Mapeo TheStatsAPI → shape canónico interno:**

| Campo canónico interno | Origen TheStatsAPI | Notas |
|---|---|---|
| `played` | `matches_played` | 1:1 |
| `wins` / `draws` / `losses` | `wins` / `draws` / `losses` | 1:1 |
| `points` | `points` | 1:1 |
| `position` | `position` | 1:1 |
| `goals_for` / `goals_against` | `goals_for` / `goals_against` | 1:1 |
| `goals_for_avg` | `goals_for / matches_played` | Derivado |
| `goals_against_avg` | `goals_against / matches_played` | Derivado |
| `form` | `form` (`"WWDLW"`) | 1:1 (string último 5) |
| `xg_for_avg`, `xg_against_avg` | ❌ **NO disponible aquí** | Mantener vía xg_recent_averages (F83.2) |
| `corners_for_avg`, `corners_against_avg` | ❌ **NO disponible aquí** | Mantener vía `/matches/{id}/stats` o API-Sports fallback |

**⚠️ Limitaciones:**
- xG y corners por equipo **no están** en este endpoint — se siguen
  obteniendo de otros lados (shotmap / fixture-stats).
- Requiere `has_team_stats=true` en la competition. Hay ligas (cup
  competitions, exhibiciones) que no lo soportan → fallback a
  API-Sports cuando falte.

**Veredicto:** ✅ Migración limpia y de bajo riesgo. Sólo cubre la parte
"liga / tabla" (puntos, posición, goles, forma). No cubre xG ni corners
por equipo — se preserva el merge actual con otras fuentes.

---

### 2.2 Bloque **b) h2h (head-to-head)** — ⚠️ Cobertura INDIRECTA

**No existe** un endpoint h2h directo en TheStatsAPI (`/fixtures/headtohead`
de API-Sports **no tiene equivalente 1:1**).

**Hack para obtener h2h:**
```
GET /football/matches?team_id={A}&status=finished&per_page=100&date_to=<today>
```
Luego, filtrar localmente los `matches` donde `home_team.id == {B}` o
`away_team.id == {B}`. Devolver los últimos N.

**Coste:**
- Una llamada por equipo (o por la pareja A+B con filtros separados).
- Si el equipo tiene > 100 partidos finished, requiere paginar.
- Cache agresivo necesario (6h+ TTL).

**Mapeo shape interno → TheStatsAPI:**

| Campo canónico | Origen TheStatsAPI |
|---|---|
| `date` | `utc_date` |
| `home_team` / `away_team` | `home_team` / `away_team` (con `id` y `name`) |
| `score` | `score` (objeto `{home, away}`) |
| `fixture.timestamp` (sort key) | `utc_date` parseado a epoch |
| `xg_available` | `xg_available` (boolean) — bonus para enriquecer luego con shotmap |

**Veredicto:** ⚠️ Migración viable pero requiere orquestación
(list+filter+paginate). Más caro en requests. **Vale la pena** si el
patrón "h2h = filtrar matches del equipo A vs equipo B" es aceptable
para el negocio. Recomiendo construir un cliente intermedio
`thestatsapi_h2h_client.py` con cache Mongo agresivo.

---

### 2.3 Bloque **e) odds (promover a primaria)** — ✅ Cobertura DIRECTA

**Endpoint:** `GET /football/matches/{match_id}/odds`

**Respuesta canónica:**
```json
{
  "data": {
    "match_id": "mt_14502",
    "bookmakers": [
      {
        "bookmaker": "Pinnacle",
        "markets": {
          "match_odds":     {"home": {"opening": "2.100", "last_seen": "2.050"}, "draw": {...}, "away": {...}},
          "btts":           {"yes": {...}, "no": {...}},
          "total_goals":    {"over_2_5": {"over": {...}, "under": {...}}},
          "match_corners":  {"over_9_5": {"over": {...}, "under": {...}}},
          "asian_handicap": {"home": {...}, "away": {...}}
        }
      }
    ]
  }
}
```

**Ventajas vs API-Sports:**
- **Line movement** explícito (`opening` vs `last_seen`).
- Estructura más limpia (un objeto por mercado vs array de mercados anidados).
- Mercados de corners y AH incluidos.

**Limitaciones:**
- Sólo disponible para upcoming dentro de **6 días** del kick-off.
- Requiere `odds_available=true` en match detail (verificar antes de
  llamar para evitar 404s ruidosos).

**Mapeo TheStatsAPI → shape canónico interno (api_sports.odds_for_fixture):**

| Mercado interno | TheStatsAPI |
|---|---|
| `Match Winner / 1X2` | `match_odds.{home,draw,away}.last_seen` |
| `Both Teams Score` | `btts.{yes,no}.last_seen` |
| `Goals Over/Under 2.5` | `total_goals.over_2_5.{over,under}.last_seen` |
| `Corners Over/Under 9.5` | `match_corners.over_9_5.{over,under}.last_seen` |
| `Asian Handicap` | `asian_handicap.{home,away}.last_seen` |
| `line_movement` (nuevo) | `opening` (todos los mercados) |

**Veredicto:** ✅ Excelente cobertura + bonus de line movement.
Migración limpia. Ya existe `thestatsapi_client.odds_for_fixture` en
`services/external_sources/thestatsapi_client.py:285` — sólo hace falta
**promoverlo a primaria** en el orquestador.

---

## 3. Recomendación de orden de migración

| Orden | Bloque | Complejidad | Riesgo de regresión | Esfuerzo (sesión) |
|---|---|---|---|---|
| **1º** | **a) team_stats** | 🟢 Baja | 🟢 Bajo | ~ 1 sesión |
| **2º** | **e) odds** (promover) | 🟢 Baja | 🟡 Medio (afecta picks) | ~ 1 sesión |
| **3º** | **b) h2h** | 🟡 Media (orquestación) | 🟡 Medio | ~ 1-2 sesiones |

**Por qué team_stats primero:**
1. Endpoint TheStatsAPI directo, 1:1 con campos limpios.
2. Función cliente `fetch_team_stats` ya existe en `thestatsapi_client.py`.
3. El normalizador (`_team_stats_root`) ya tolera múltiples fuentes → el
   cambio es de prioridad, no de schema.
4. Sin orquestación: una llamada por equipo.
5. Sin ventanas de tiempo (no como odds).
6. **Excelente ensayo general** para validar el patrón "TheStatsAPI
   primaria + API-Sports fallback con flag" antes de tocar odds o h2h.

---

## 4. Patrón de migración (aplicado por bloque)

Para cada bloque:

1. **Adapter:** crear `services/external_sources/thestatsapi_<bloque>_adapter.py`
   que llama al endpoint TheStatsAPI y devuelve el **shape canónico
   interno** (no el shape crudo de la API).
2. **Resolver IDs:** TheStatsAPI usa IDs string (`tm_8923`, `sn_7210`).
   Hay que mapear nuestros IDs internos (int de API-Sports). Reutilizar
   el match-resolver existente (`thestatsapi_enrichment.resolve_match_id`).
3. **Orquestador `data_ingestion.py`:** invertir la prioridad:
   ```python
   if ENABLE_THE_STATS_API:
       stats = await thestatsapi_team_stats_adapter.fetch(...)
   if not stats and ENABLE_API_SPORTS_FALLBACK:
       stats = await api_football.team_statistics(...)
   ```
4. **Flag de control:**
   - `ENABLE_THE_STATS_API` (ya existe) — primaria.
   - `ENABLE_API_SPORTS_FALLBACK` (nuevo, default `true`) — fallback.
   - `ENABLE_API_SPORTS_FALLBACK=false` → deprecación completa.
5. **Tests:**
   - Adapter happy-path + fail-soft (404, timeout, disabled).
   - Orquestador: TheStatsAPI exitoso → no llama API-Sports.
   - Orquestador: TheStatsAPI 404 → cae a API-Sports.
   - Orquestador: ambos fail → shape `{available: false, ...}`.
6. **Provenance:** anotar `source: "thestatsapi"` vs
   `source: "api_sports_fallback"` en el campo `_provenance` del
   resultado para auditoría.

---

## 5. Estado actual

- ✅ F83.2 (xG shotmap) completado — primer bloque migrado bajo este patrón.
- 🟢 F84.a (team_stats) — **siguiente** según recomendación.
- ⏸️ F84.e (odds) — pendiente.
- ⏸️ F84.b (h2h) — pendiente.
