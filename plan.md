# Plan — Phases F58–F93 (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.
> **Estado histórico:** ✅ F58–F70 completadas.
> **Estado actual (resumen):** ✅ F58–F70 + F74 (+post v2/v2.5) + F82/F82.1/F82.1-adjust + F83/F83.1/F83.2 + P2 + F82.2 + P4.1 + F84.a/b/e + F85 (+Phase 2) + F86/F87/F88 (Sprint F86.2) + F89 (Sprint F86.1) + F90 (Sprint F83-update) + F91 (MLB QCM Engine puro) + F92 (MLB QCM Applier + Wiring) + F93 (Corners cascade) + Bugfix Upcoming Filter + Fixture Hard Gate + Pipeline Debug Instrumentation + ✅ **F87 (Football fixture discovery cascade) COMPLETADA** + ✅ **F87.1 (Fixture Discovery Contract Fix + Visible Audit + Parte 1.5 upstream audit) COMPLETADA** + ✅ **MLB-F93 (Manual Odds Override Reprice + UI Refresh) COMPLETADA**.
>
> **Idioma operativo:** Español.

---

## 1) Objetivos

### Objetivos originales (F58)
- Implementar un **cross L5 vs L15** para fútbol (goles, xG, xGA, tiros, SOT, corners) con 7 perfiles y deltas simétricos.
- Añadir **ingestión híbrida** para hidratar stats de jugador usadas por props:
  - StatMuse primario (shots/SOT/minutos)
  - FBref (pases/tackles/fouls/cards/xG) cuando sea accesible
  - Understat como último recurso
- Implementar **Player Props Discovery Moneyball** (tiers + gates) con degradación fail-soft.
- Integrar en el flujo football existente con override contextual.
- Añadir smoke tests y mantener suite global verde.
- (P2) UI wiring: panel independiente para Cross + Override + Player Props.

### Objetivos nuevos / extendidos (F69–F74)
- Editorial interno específico por partido (no genérico).
- Scrapers externos (Forebet / Sportytrader) como fallback.
- Normalización de identidad de mercado y reconciliación interno vs externo.
- Auditoría de predicciones externas contra fuerza del rival.
- Guardrails para **mercados UNKNOWN** (no edge, no trap, no discard).
- F74: **schema canónico** para enriquecimiento fútbol + probabilidades estimadas.
- F74-post: **adaptadores** para eliminar fragmentación de datos anidados.
- F74-post v2: **fallback de odds con TheStatsAPI** (incluye opening/last_seen → line movement sin snapshots históricos).
- F74-post v2.5: **line movement desde día 1** usando `opening` TheStatsAPI + `last_seen`.

### Objetivos nuevos / extendidos (F82)
- **H2H rico**: dejar de mostrar “se identifican N enfrentamientos…” y renderizar resultados concretos + señales.
- **Córners con fuente secundaria real**: ingestión de stats de córners usando **365Scores** como fallback (a través de scrape.do) y persistencia consistente.
- **Recomendación conservadora de córners**: no recomendar si `corners.available=false` o si solo hay córners actuales sin tendencia.

### Objetivos nuevos / extendidos (F82.1) — Protección de timeouts (crítico)
- Separar enriquecimiento en:
  - **FAST tier obligatorio (inline)**: H2H desde `h2h_recent` + corners desde datos presentes. **Cero HTTP externo**.
  - **EXTERNAL tier opcional**: 365Scores (scrape.do + resolver IDs). **Nunca inline por defecto**.
- Añadir feature flags + timeouts duros para proteger el job principal.

### Objetivos nuevos / extendidos (F83) — Intervención manual de mercado + cuota
- Cuando haya `REQUIRES_MARKET_IDENTIFICATION`, habilitar intervención manual (cuota manual, selector de mercado, recalcular).
- Backend con endpoint POST para reprice + endpoint GET con catálogo de mercados.

### Objetivos nuevos / extendidos (F83.2 / Bloque E) — xG reciente L1/L5/L15 desde shotmap (TheStatsAPI)
- Calcular promedios xG no-penal (a favor / en contra) L1/L5/L15 por equipo usando shotmap TheStatsAPI.
- Arquitectura **background-first** con cache + timeouts.
- Señales contextuales (nunca pick-binding) + señales de cobertura/muestra parcial.

### Objetivos nuevos / extendidos (P4.1) — Estabilidad de tests UI (LiveReevalPanel)
- Mantener suite FE estable (alinear tests con copy y flujos reales).

### Objetivos nuevos / extendidos (F84) — Migración estructural API-Sports → TheStatsAPI (prioridad-inversa)
- Migrar bloques estructurales fútbol a TheStatsAPI como primaria, manteniendo API-Sports como fallback:
  - F84.a Team Stats ✅
  - F84.b H2H ✅
  - F84.e Odds + line movement ✅
- Flags + auditoría `_provenance_*`.

### Objetivos nuevos / extendidos (F85) — Public xG Enrichment (FBref + Forebet vía scrape.do)
- Scraping fail-soft y background-first con endpoints run-now/background/status.
- UI panel para disparo y render.
- Phase 2: resolver FBref search-page + fuzzy matching ✅.

### Objetivos nuevos / extendidos (F86) — H2H Decision Policy (puro Python)
- Definir cuándo H2H puede influir en scoring vs. cuándo es solo narrativo.
- Output: `h2h_context` enriquecido + `h2h_decision` (points_by_market + signals).

### Objetivos nuevos / extendidos (F87) — Cableado quirúrgico en `_enrich_football`
- Integrar H2H decision + xG recent averages (background) sin bloquear el camino crítico.

### Objetivos nuevos / extendidos (F88 / Sprint F86.2) — Editorial Consumer
- Editorial output y UI consumen `h2h_decision` + `xg_recent_averages`.
- Scoring aplica bump H2H al mercado (clamp +8 + guards).

### Objetivos nuevos / extendidos (F89 / Sprint F86.1) — Calibración H2H rules + guards explícitas
- Recalibrar `H2H_POINT_RULES` contra baselines típicas (más robusto).
- Introducir `get_active_rules()` con override por env (JSON) leído en tiempo de llamada.
- Agregar polarity guard explícito (OVER/UNDER por línea + BTTS YES/NO) con auditoría.
- Agregar sample guard por regla (`min_sample`) + señal `LOW_SAMPLE_H2H_SIGNAL`.
- Agregar DNB overlap guard suave (HOME_DNB + AWAY_DNB no es hard-conflict).
- Agregar cap agregado de puntos H2H (`MAX_H2H_POINTS_TOTAL=8`).
- Mantener back-compat con consumers/editorial UI.

### Objetivos nuevos / extendidos (F90 / Sprint F83-update) — Corners cascade con diagnóstico estructurado (Scrape.do)
- Eliminar el mensaje genérico **"Falló la carga de córners"** y reemplazarlo por mensajes específicos según proveedor/etapa/reason_code.
- Exponer endpoint: `GET /api/football/corners/debug?match_id=...`
- Añadir UI debug de córners.

### Objetivos nuevos / extendidos (F91) — MLB Quality Contact Matchup Engine (módulo puro)
- Detectar discrepancias entre calidad de contacto ofensivo vs vulnerabilidad del abridor vs percepción por ERA.
- **No generar picks automáticos**: solo output explicable con señales.

### Objetivos nuevos / extendidos (F87.1) — Fixture Discovery Contract Fix + Visible Audit (con Parte 1.5 upstream)
**Objetivo global:** eliminar “pérdidas invisibles” de fixtures y permitir diagnóstico end-to-end.
**Estado:** ✅ COMPLETADO.

### Objetivos nuevos / extendidos (MLB-F93) — Manual Odds Override Reprice + UI Refresh
**Problema:** al guardar cuota manual (especialmente cuando el pick no se encuentra en runs recientes) antes solo se persistía override y la UI no cambiaba.

**Objetivo:** al guardar cuota manual, el sistema SIEMPRE devuelve un resultado accionable y, si hay contexto suficiente, **repricing inmediato** + **refresh visual en MatchCard** sin regenerar picks.

**Estado:** ✅ COMPLETADO.

---

## 2) Implementación (fases)

### Fase 1 — POC (Aislamiento): Scraping/ingestión de stats de jugador
**(COMPLETADO)** — sin cambios.

### Fase 2 — V1 App Dev: Football Team Profile Cross (L5 vs L15)
**(COMPLETADO)** — sin cambios.

### Fase 3 — V1 App Dev: Football Player Props Discovery (Moneyball)
**(COMPLETADO)** — sin cambios.

### Fase 4 — Integración en Football pipeline (override incluido)
**(COMPLETADO)** — sin cambios.

### Fase 5 — UI Wiring (P2)
**(COMPLETADO)** — sin cambios.

### Fase 6 — Prueba con datos reales (P2)
**(COMPLETADO)** — sin cambios.

### Fase 7 — Smoke tests + verificación final
**(COMPLETADO)** — sin cambios.

---

## Phase F87.1 — Football Fixture Discovery Contract Fix + Visible Audit (COMPLETED ✅)
**(COMPLETADO)** — ver sección F87.1 existente para detalles (sin cambios).

---

# Phase MLB-F93 — Manual Odds Override Reprice + UI Refresh (COMPLETED ✅)

## Estado
✅ **Completado end-to-end**: backend (reprice + lookup + debug + persistencia) + frontend (refresh visual en MatchCard) + tests.

## Implementación realizada (resumen)

### Backend
- ✅ Nuevo módulo puro fail-soft:
  - `backend/services/mlb_manual_odds_reprice.py`
  - API: `reprice_mlb_pick_with_manual_odds()` + `build_minimal_pick_context_from_match_doc()`.
  - Decisiones: `VALUE | NO_VALUE | WATCHLIST | MANUAL_ODDS_ONLY | INVALID`.
  - Métricas: `implied_probability`, `model_probability`, `fair_odds`, `edge`, `edge_pct`, `ev`.
  - Reason codes: `MANUAL_ODDS_REPRICE_APPLIED`, `MANUAL_ODDS_OVERRIDE_USED`, `EDGE_*`, `EV_*`, `MODEL_PROBABILITY_MISSING`, `PICK_CONTEXT_*`.

- ✅ Búsqueda del contexto ampliada:
  - `_locate_pick_multikey` conserva estrategias previas (pick_id → alt ids → teams/date).
  - **Nuevo:** fallback a `matches` collection para reconstruir `minimal_pick_context` cuando el pick no está en `pick_runs`.

- ✅ Endpoint POST actualizado (contrato F93 + back-compat):
  - `POST /api/mlb/picks/{pick_id}/manual-odds`
  - **Nuevo contrato F93 (siempre presente):**
    - `status: REPRICED | OVERRIDE_SAVED_ONLY | PICK_NOT_FOUND | ERROR`
    - `reprice: {available, decision, edge, edge_pct, fair_odds, implied_probability, model_probability, ev, confidence_before/after, reason_codes, rationale}`
    - `message_user`, `message_debug`, `next_action`
  - **Back-compat mantenida:**
    - `attached_to_pick`, `fallback_override_created`, `value_status`, `manual_edge_pct`, `manual_odds`, `tried_keys`, `message`, etc.

- ✅ Persistencia ampliada en `pick_runs` cuando el pick existe:
  - `manual_odd`, `odds_source=USER_MANUAL_OVERRIDE`, `odds_status=MANUAL`
  - `reprice`, `value_status`, `edge`, `ev`, `fair_odds`, `implied_probability`
  - `manual_odds_updated_at`, `manual_reprice_promoted`.
  - Mantiene también los campos legacy (`manual_*`).

- ✅ Nuevo endpoint de debug:
  - `GET /api/mlb/manual-odds/debug?match_id=...&pick_id=...&game_pk=...`
  - Respuesta: `lookup_attempts`, `override_found`, `can_reprice`, `missing_fields`, `final_status`.

### Frontend
- ✅ `InlineManualOddsInput.jsx` reescrito:
  - Loading: “Recalculando con cuota manual…”
  - Switch por `status`:
    - `REPRICED`: mensajes específicos (VALUE/NO VALUE/WATCHLIST)
    - `OVERRIDE_SAVED_ONLY`: explicación + CTAs **Refrescar card / Regenerar análisis / Ver debug**
  - Nuevo callback: `onReprice(payload)` para notificar al card.

- ✅ Integración en `MatchCard.jsx` (lugar correcto):
  - Estado local `manualReprice`.
  - `displayOddsRange` usa `manualReprice.manual_odd` si existe.
  - Badges: VALUE/NO VALUE/WATCHLIST/SOLO INFO con edge%.
  - Chip EV + mensaje de decisión.
  - No requiere regenerar picks ni recargar la página.

## Tests y validación (zero-regression)
- ✅ Backend:
  - +14 tests nuevos (`11 obligatorios + 3 sanity`).
  - Suite completa: **2956 passed, 2 skipped** (baseline anterior 2942 → +14).
- ✅ Frontend:
  - +5 tests nuevos (los 5 obligatorios).
  - Suite completa: **130 passed** (baseline anterior 125 → +5).

## Variables de entorno
- `MLB_MANUAL_VALUE_EDGE_THRESHOLD` (default `0.03`).
- `MLB_MANUAL_WATCHLIST_TOLERANCE` (default `0.02`).

---

## 3) Pendientes y siguientes pasos (post-MLB-F93)

### Pendientes P0 (actual)
- Ninguno (MLB-F93 cerrado).

### Pendientes no bloqueantes
- (F84.c) lineups / injuries — fuera de scope inicial, requiere confirmar cobertura TheStatsAPI.
- (F84.d) standings — fuera de scope inicial.
- (P3) Expandir `team_name_translations.py` para clubes UCL/UEL.

### Futuras mejoras recomendadas (global)
- Backtest de la calibración F86.1 con ≥ 30 picks reales con H2H aplicado para ajustar thresholds.
- Implementar calibrador offline cuando exista una fuente estable.

---

## 6) Validación esperada (estado actual)

- Suites actuales:
  - Backend: **2956 passed, 2 skipped**.
  - Frontend: **130 passed**.

- Reglas de operación:
  - Siempre usar `yarn` (no `npm`).
  - Arquitectura fail-soft: no levantar excepción sin convertirla a auditoría/razón.
  - Mantener back-compat en contratos de respuesta cuando el FE dependa de fields legacy.

- Flags / env relevantes:
  - ✅ **F87.1:** `DISCOVERY_DROPPED_SAMPLE_CAP` (default `3`).
  - ✅ **MLB-F93:** `MLB_MANUAL_VALUE_EDGE_THRESHOLD` (default `0.03`).
  - ✅ **MLB-F93:** `MLB_MANUAL_WATCHLIST_TOLERANCE` (default `0.02`).
