# Plan — Phase F58 (Football L5 vs L15 Profile Cross + Player Props Discovery)

## 1) Objetivos
- Implementar un **cross L5 vs L15** para fútbol (goles, xG, xGA, tiros, SOT, corners) con 7 perfiles y deltas simétricos.
- Añadir **ingestión híbrida** para hidratar stats de jugador usadas por props:
  - StatMuse primario (rápido y estable para shots/SOT/minutos)
  - FBref para enriquecer mercados de volumen (pases/tackles/fouls/cards/xG) cuando sea accesible
  - Understat como último recurso para xG/shots/sot cuando aplica
- Implementar **Player Props Discovery Moneyball** (tiers + gates) con degradación fail-soft.
- Integrar en el flujo football existente con un modo más agresivo: **override contextual** cuando el cross sea “muy fuerte”.
- Añadir **smoke tests** (mínimos) y mantener la suite global verde (cero regresiones).
- (P2) **UI wiring**: panel independiente en cards football para visualizar Cross + Override + Player Props.

**Estado actual:**
- ✅ Backend Phase F58 completado (módulos core + integración + tests).
- ✅ UI panel independiente completado y cableado en `MatchCard` (fail-soft).
- ✅ Probe real EPL ejecutado (StatMuse OK; FBref directo 403 sin Bright Data).
- ✅ FBref scraper implementado (con caveat anti-bot) + smart merge.
- ✅ Test suite verde: **1744/1744 passing**.

## 2) Implementación (fases)

### Fase 1 — POC (Aislamiento): Scraping/ingestión de stats de jugador (core frágil)
**Por qué aquí:** el scraping es el punto más propenso a romperse (estructura HTML/anti-bot) y condiciona toda la discovery.

**User stories (POC)**
1. Como caller, quiero pedir stats de un jugador y obtener un dict normalizado aunque falten campos.
2. Como caller, quiero que si StatMuse falla, el sistema intente fallback automáticamente.
3. Como caller, quiero que si todo falla, el ingestor responda fail-soft sin romper el pipeline.
4. Como dev, quiero poder ejecutar un script local que pruebe jugadores reales end-to-end.
5. Como sistema, quiero cachear resultados para no golpear la fuente repetidamente.

**Pasos (COMPLETADO + ACTUALIZADO)**
- ✅ Crear `backend/services/football_player_stats_ingestor.py`.
  - ✅ API: `hydrate_player_stats(*, player_name: str, team: str | None = None, league: str | None = None) -> dict`.
  - ✅ Salida estándar (siempre):
    - `{"available": bool, "source": str, "confidence_penalty": int, "minutes_sample": int|None, "stats": {...}, "raw": {...}}`.
  - ✅ Normaliza métricas: `shots_p90`, `sot_p90`, `passes_p90`, `tackles_p90`, `fouls_p90`, `cards_p90`, `xg_p90`, `minutes_p_game`, `minutes_sample`.
  - ✅ Implementación (cadena final):
    - ✅ **StatMuse scraping** (primario): HTMLParser + cache + fail-soft.
    - ✅ **FBref scraper** (terciario/enriquecimiento):
      - Search → player page → tabla Standard Stats (usa `data-stat` para robustez).
      - Prefer Bright Data cuando esté disponible; `direct_fetch` como último intento.
      - **Caveat**: FBref puede devolver 403 desde IPs de datacenter (sin Bright Data).
    - ✅ **Understat fallback** (último recurso): xG/shots/sot cuando se dispone de helper.
  - ✅ **Smart merge**:
    - Si StatMuse devuelve stats parciales → se intenta FBref y se rellenan `None` sin sobre-escribir valores de StatMuse.
    - Si StatMuse viene completo → NO se consulta FBref (politeness/perf).
    - `source` ∈ {`statmuse`, `fbref`, `understat`, `statmuse+fbref`, `unavailable`}.
  - ✅ Cache in-memory TTL **6h**.

### Fase 2 — V1 App Dev: Football Team Profile Cross (L5 vs L15)
**User stories**
1. Como analista, quiero ver un perfil de cruce L5 vs L15 para entender si el partido cambió de régimen.
2. Como sistema, quiero deltas de confidence/fragility consistentes con el patrón MLB.
3. Como UI, quiero un entry “pattern_alignment” visual para explicar el cross.
4. Como caller, quiero un resultado fail-soft si faltan inputs.
5. Como trader, quiero que en señales muy fuertes se pueda **override** (según reglas).

**Pasos (COMPLETADO)**
- ✅ Crear `backend/services/football_team_profile_cross.py`:
  - ✅ `classify_team_football_profile(...)`.
  - ✅ `compute_combined_football_profile_cross(home, away)` → 7 perfiles.
  - ✅ `apply_profile_cross_to_pick(...)`:
    - Override gating:
      - Solo `STRONG_UNDER_CROSS`, `STRONG_OVER_CROSS`, `CORNERS_OVER_CROSS`.
      - Umbral “muy fuerte”: `confidence_delta >= 10`.
      - Solo si el cross contradice el pick.
  - ✅ `build_pattern_alignment_entry(...)` visual-only (`visual_only=True`).

### Fase 3 — V1 App Dev: Football Player Props Discovery (Moneyball)
**User stories**
1. Como usuario, quiero props “aburridas” (alto volumen) en vez de longshots.
2. Como sistema, quiero rechazar props con prob baja aunque parezcan +EV.
3. Como caller, quiero una lista ordenada por edge_score con metadata completa.
4. Como sistema, quiero degradar a “no props” si faltan stats del jugador.
5. Como usuario, quiero que “Player to score” solo aparezca si es ultra-élite (edge≥90 y fragility≤35).

**Pasos (COMPLETADO)**
- ✅ Crear `backend/services/football_player_props_discovery.py`:
  - ✅ Tier 1: `SHOTS_OVER`, `SOT_OVER`, `PASSES_OVER`, `TACKLES_OVER`.
  - ✅ Tier 2: `FOULS_OVER`, `CARDS_OVER`.
  - ✅ Tier 3: `PLAYER_TO_SCORE` (gate duro: `edge_score>=90` & `fragility<=35`).
  - ✅ Poisson λ y Moneyball gates (`min_prob=0.55`, `min_edge_pts=4.0`, `longshot_floor=0.50`).
  - ✅ Consume `hydrate_player_stats` por defecto (o inyectable).

### Fase 4 — Integración en Football pipeline (override incluido)
**User stories**
1. Como sistema, quiero que el cross pueda modificar/override el pick final cuando sea muy fuerte.
2. Como UI, quiero ver claramente cuándo hubo override y por qué.
3. Como sistema, quiero que fallos de scraping no rompan el refresh de football.
4. Como analista, quiero poder auditar reason_codes y fuentes.
5. Como QA, quiero que los cambios no afecten baseball/basketball.

**Pasos (COMPLETADO)**
- ✅ Crear `backend/services/football_phaseF58_integration.py`:
  - Deriva inputs L5/L15 desde `recent_fixtures`.
  - Adjunta `combined_football_profile_cross` al payload.
  - Aplica deltas simétricos + escribe `football_profile_cross_applied`.
  - Añade entry visual-only en `pattern_alignment.entries`.
  - **No muta** el market final: emite `override` como sugerencia auditable.
- ✅ Cableado en `backend/services/football_moneyball/football_pattern_matcher.py` (paso 5, antes de persist).

### Fase 5 — UI Wiring (P2) — Panel independiente Cross + Override + Player Props
**User stories**
1. Como usuario, quiero visualizar el cross L5 vs L15 y entender qué apoya.
2. Como usuario, quiero ver un aviso claro cuando existe un override sugerido.
3. Como usuario, quiero ver props Moneyball por jugador (tiers) cuando estén disponibles.
4. Como sistema, el panel no debe romper cards sin payload (self-hide).

**Pasos (COMPLETADO)**
- ✅ Crear `frontend/src/components/FootballProfileCrossPropsPanel.jsx` (diseño propio, no replica MLB):
  - Header + sección Cross + sección Override + sección Player Props.
  - Paleta emerald/cyan (sin purple), microtipografía estilo terminal, `data-testid`.
  - Self-hide si no hay datos.
- ✅ Integrar en `frontend/src/components/MatchCard.jsx` para `sport === 'football'`.
- ✅ Verificación de compilación con esbuild.

### Fase 6 — Prueba con datos reales (P2) — validar `hydrate_player_stats`
**User stories**
1. Como operador, quiero confirmar que StatMuse funciona en el entorno.
2. Como operador, quiero saber si FBref está bloqueado por anti-bot y qué requiere.
3. Como sistema, quiero que el chain degrade sin fallar.

**Pasos (COMPLETADO + DOCUMENTADO)**
- ✅ Crear `backend/scripts/test_phaseF58_real_player.py`.
- ✅ Ejecutar probe EPL (Haaland, Saka, Salah):
  - StatMuse **OK** (200) → shots_p90/sot_p90/minutes_sample.
  - FBref **403** sin Bright Data (anti-bot) → se documenta como caveat.
- ✅ Persistir resultados en `backend/scripts/out/phaseF58_real_player_probe.json`.

### Fase 7 — Smoke tests + verificación final
**User stories**
1. Como dev, quiero que `pytest` siga pasando sin regresiones.
2. Como dev, quiero smoke tests que validen contratos y merges.

**Pasos (COMPLETADO + ACTUALIZADO)**
- ✅ Smoke tests existentes F58 (49).
- ✅ Añadidos 11 tests FBref + merge:
  - `tests/test_phaseF58_fbref_scraper_smoke.py`
- ✅ Pytest suite completa: **1744/1744 passing** (0 regresiones).

## 3) Next Actions (orden de ejecución)
**Estado actual: DONE para backend + UI panel. Próximos pasos recomendados (P2/P3):**
1. (P2) **RTL tests** del nuevo `FootballProfileCrossPropsPanel.jsx`.
2. (P2) **Wiring del payload de props en football pipeline**:
   - Actualmente el panel espera `pick.player_props_discovery`.
   - Falta adjuntar automáticamente `discover_player_props(...)` en el flujo que construye el match/pick payload (por ejemplo en `attach_football_intelligence_to_payload` o en el stage que arma la card).
3. (P3) **Configurar Bright Data en producción** para activar FBref enrichment:
   - Requiere `BRIGHTDATA_API_KEY` + `BRIGHTDATA_CUSTOMER_ID` (según `services/external_sources/base.py`).
4. (P3) **Ampliar StatMuse slugs** para cubrir pases/tackles si StatMuse soporta endpoints equivalentes (reduce dependencia en FBref).
5. (P3) **Backtest del override gating** (tasa de override, hit-rate/ROI) antes de aplicar auto-flip en producción.

## 4) Criterios de éxito
- ✅ Ingestor devuelve siempre un dict normalizado; en fallo devuelve `available=False` y no rompe.
- ✅ Cross profile produce uno de los 7 perfiles cuando hay inputs suficientes; si no, `available=False`.
- ✅ Override ocurre **solo** para perfiles permitidos y umbral “muy fuerte” (y solo si contradice).
- ✅ Player props: Tier 1/2 generables con stats; Tier 3 solo si `edge_score≥90` y `fragility≤35`.
- ✅ UI panel se renderiza sin romper cards (self-hide) y respeta design system.
- ✅ `pytest` completo pasa (sin regresiones) y los smoke tests cubren parsing/merge FBref.