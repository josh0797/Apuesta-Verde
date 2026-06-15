# Plan — Phases F58–F91 (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.
> **Estado histórico:** ✅ F58–F70 completadas.
> **Estado actual (resumen):** ✅ F58–F70 + F74 (+post v2/v2.5) + F82/F82.1/F82.1-adjust + F83/F83.1/F83.2 + P2 + F82.2 + P4.1 + F84.a/b/e + F85 (+Phase 2) + F86/F87/F88 (Sprint F86.2) + F89 (Sprint F86.1) + F90 (Sprint F83-update) + F91 (MLB QCM Engine puro) + F92 (MLB QCM Applier + Wiring) + F93 (Corners cascade) + Bugfix Upcoming Filter + Fixture Hard Gate + Pipeline Debug Instrumentation + ✅ **F87 (Football fixture discovery cascade: TheStatsAPI → API-Football → ESPN → Sofascore PW → scrape.do + Unknown Bucket + MLB Isolation) COMPLETADA**.
> **Nuevo estado:** ✅ **F87.1 (Fixture Discovery Contract Fix + Visible Audit + Parte 1.5 upstream audit) COMPLETADA**.
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
- Eliminar el mensaje genérico **"Falló la carga de córners"** y reemplazarlo por mensajes específicos según:
  - proveedor
  - etapa (`ID_RESOLUTION`, `FETCH_STATS`, `FETCH_PAGE`, `PARSE_HTML`, `NORMALIZE`, etc.)
  - `reason_code` (token ausente, breaker abierto, HTTP 403/429/503, HTML sin stats, stats sin córners, etc.)
- Exponer un endpoint de diagnóstico:
  - `GET /api/football/corners/debug?match_id=...`
- Añadir UI para debug:
  - botón **"Ver debug de córners"**
  - dialog con cascade order usado, estado scrape.do (token+breaker) y providers_checked.
- Mantener el order por defecto de F82.2 (TSA→APS→365) y habilitar un order alternativo bajo flag:
  - `ENABLE_F83_CASCADE_ORDER=true` → APS→365→TSA

### Objetivos nuevos / extendidos (F91) — MLB Quality Contact Matchup Engine (módulo puro)
- Detectar discrepancias entre:
  - calidad real del contacto ofensivo (xwOBA, sweet-spot%, barrel%, hard-hit%)
  - vulnerabilidad del abridor (xERA, xwOBA allowed, barrel% allowed, hard-hit% allowed)
  - percepción pública basada en ERA
- **No generar picks automáticos**: solo output explicable con señales.
- Entregar un **módulo puro** con:
  - lineup_contact_quality (ponderado por orden al bate)
  - pitcher_vulnerability (0–100)
  - matchup_contact_factor y contact_mismatch_score
  - detector de regresión (xERA − ERA)
  - señales: `MATCHUP_CONTACT_ADVANTAGE`, `PITCHER_BARREL_REGRESSION_RISK`, `ERA_UNDERSTATES_DAMAGE`, `TOP_ORDER_THREAT`, `OVER_CONTACT_WARNING`
  - Datos por bateador derivados por defecto; flag `QCM_LINEUP_PER_BATTER=true` para per-batter real en el futuro

### Objetivos nuevos / extendidos (F87.1) — Fixture Discovery Contract Fix + Visible Audit (con Parte 1.5 upstream)
**Objetivo global:** eliminar “pérdidas invisibles” de fixtures en la cadena real y permitir diagnóstico end-to-end:

**Adapter → Contract → Discovery cascade → Competition filter → Unknown cap → Enrichment → UI**

Requisitos:
- Arquitectura fail-soft estricta (sin 500s; razones trazables).
- Contrato de fixtures uniforme (shape API-Football) antes de cualquier merge/dedupe.
- Auditoría visible que distinga:
  - *adapter returned empty* vs.
  - *adapter devolvió fixtures pero el contract los rechazó*.
- UI: cuando `Analizados=0` y hubo fixtures raw, mostrar “fixtures rechazados por contract”, no “no hay partidos”.

**Estado:** ✅ COMPLETADO (ver sección F87.1 para detalles y resultados).

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

### Fase 5 — UI Wiring (P2) — Panel independiente Cross + Override + Player Props
**(COMPLETADO)** — sin cambios.

### Fase 6 — Prueba con datos reales (P2)
**(COMPLETADO)** — sin cambios.

### Fase 7 — Smoke tests + verificación final
**(COMPLETADO)** — sin cambios.

---

## Phase F69 — Fix análisis editorial interno match-specific (COMPLETED ✅)
**(COMPLETADO)** — ver historial en este mismo archivo.

---

## Phase F70 — Reemplazo externo (Sportytrader / Forebet) (COMPLETED ✅)
**(COMPLETADO)** — ver historial en este mismo archivo.

---

# Phase F74 — Parcial: Unified Football Enrichment + Protected Floor Recalibration (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F74-post — Resolver ingesta interna, market identity y puente TheStatsAPI/API-Sports (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F74-post v2 — TheStatsAPI Odds Fallback Wiring (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F74-post v2.5 — Opening Odds → Line Movement Wiring (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F82 — Rich H2H Context + 365Scores Corners Ingestion (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F82.1 — Non-blocking H2H/Corners Enrichment + Job Timeout Protection (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F83 — Manual Market Identity + Manual Odds Injection (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F82.1-adjust — Manual/Background Corners Enrichment Endpoints (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F83.1-fix — Manual Market Identity match_id isolation + Data Availability Sections (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase P2 — `infer_original_pick_side` (4-source cascade) (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F82.2 — Scores24 → 365Scores cross integrator + provider re-order (Backend COMPLETED ✅ / Frontend COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F83.2 (Bloque E) — xG L1/L5/L15 desde TheStatsAPI shotmap (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase P4.1 — Fix tests preexistentes LiveReevalPanel (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F84 — Migración estructural API-Sports → TheStatsAPI (prioridad-inversa) (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F85 — Public xG Enrichment (FBref + Forebet vía scrape.do) (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F86 — H2H Decision Policy (COMPLETED ✅)
**(COMPLETADO)** — ver fases F87/F88/F89 para wiring y calibración.

---

# Phase F87 — Cableado quirúrgico H2H Decision + xG-recent background dispatch (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F88 — Sprint F86.2: Editorial Consumer para H2H decision + xG recent averages (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F89 — Sprint F86.1: Calibración H2H_POINT_RULES + explicit polarity guard + sample guard (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F90 — Sprint F83-update: Corners cascade con diagnóstico estructurado vía Scrape.do + flag F83 cascade order (COMPLETED ✅)
**(COMPLETADO)** — ver historial F90 en este mismo archivo.

---

# Phase F91 — MLB Quality Contact Matchup Engine (módulo puro) (COMPLETED ✅)

## Estado: ✅ COMPLETADA

(Detalles del engine puro mantenidos arriba; ver `mlb_quality_contact_matchup.py` + 36 tests focales.)

---

# Phase F92 — MLB QCM Signals Applier + Pipeline Wiring (COMPLETED ✅)

## Estado: ✅ COMPLETADA

---

# Phase F93 — Corners cascade migration (TotalCorner + FootyStats vía scrape.do) (COMPLETED ✅)

## Estado: ✅ COMPLETADA

---

# Bugfix — Upcoming filter rechaza partidos terminados / aplazados / cancelados (COMPLETED ✅)

## Estado: ✅ COMPLETADA

---

# Fixture Time/Status Hard Gate (PREMATCH_BUFFER_MINUTES) (COMPLETED ✅)

## Estado: ✅ COMPLETADA

---

# Pipeline Debug Instrumentation + /api/diagnostics/api-health (COMPLETED ✅)

## Estado: ✅ COMPLETADA

---

# Phase F87 — Football Fixture Discovery Cascade (COMPLETED ✅)

## Estado: ✅ COMPLETADA

---

# Phase F87.1 — Football Fixture Discovery Contract Fix + Visible Audit (COMPLETED ✅)

## Estado
✅ **Completada end-to-end**: contract + adapters + auditoría + endpoint + UI + tests.

## Cambios implementados (resumen)

### (A) Contract 	`backend/services/football_fixture_contract.py`
- Contract canónico API-Football garantizado a salida de discovery.
- Soporta múltiples shapes upstream para resolver nombres home/away:
  - `teams.home/away.name`
  - `home_team/away_team.name`
  - `homeTeam/awayTeam.name`
  - `competitors[]` (detección home/away por `homeAway`, `isHome`, `home`, `side` + fallback por orden)
  - `participants.home/away.name`
  - `homeCompetitor/awayCompetitor.name`
  - `localTeam/visitorTeam.name`
  - `team1/team2.name`
- `normalize_bucket()` ahora emite auditoría enriquecida:
  - `raw_count / kept_count / dropped_count`
  - `reason_codes` agregados
  - `top_reason`
  - `dropped_samples` (cap por adapter) con evidencia:
    - `home_candidates` / `away_candidates` / `kickoff_candidates`
    - `raw_id`, `league`, `reason_code`
  - Distingue:
    - `adapter_returned_empty=true` + `ADAPTER_RETURNED_EMPTY`
    - `had_raw_but_all_rejected=true`
- Cap configurable:
  - `DISCOVERY_DROPPED_SAMPLE_CAP` (default `3`).

### (B) Adapters upstream
- `backend/services/external_sources/thestatsapi_fixtures_adapter.py`
- `backend/services/external_sources/sofascore_fixtures_adapter.py`

Cambios:
- Ya **no fabrican** placeholders "Home"/"Away" cuando faltan nombres.
- Permiten que el contract descarte y emita evidencia completa (diagnóstico real).

### (C) Endpoint debug 	`GET /api/football/discovery/debug`
- Mantiene back-compat:
  - `counts_raw`, `counts_after_shape_normalization`, `shape_audit`.
- Agrega payload Parte 1.5:
  - `adapter_audit` por fuente (raw/normalised/dropped/top_reason/dropped_samples...)
  - `raw_total`, `normalised_total`
  - `had_raw_but_all_rejected`, `any_adapter_returned_empty`
  - `ui_message` diferenciado (NO "no hay partidos" cuando raw>0 y normalised=0).

### (D) UI — Sheet lateral derecho
- Nuevo componente:
  - `frontend/src/components/DiscoveryDebugSheet.jsx`
- Integración en dashboard:
  - `frontend/src/pages/DashboardPage.jsx`
  - CTA "Ver debug de discovery" aparece solo cuando:
    - `sport === 'football'` y `total_analyzed === 0`.
  - Sheet muestra:
    - Totales (raw / normalised / final)
    - Mensaje diferenciado por diagnóstico
    - Tabla por adapter con top_reason y dropped_samples (hasta cap)
    - Botón "Forzar refresh" (`?refresh=true`).

## Tests y validación
- Backend:
  - ✅ `pytest` completo: **2942 passed, 2 skipped** (baseline anterior: 2918) → **zero regresión**.
  - Nuevos tests:
    - 15 tests contract/discovery base (`test_f87_1_fixture_contract.py`)
    - 9 tests upstream audit (`test_f87_1_upstream_audit.py`)
- Frontend:
  - ✅ `yarn craco test`: **125/125 passed**.
- Validación del endpoint (preview):
  - ✅ Caso real verificado: **API-Football 78 fixtures normalizados** + TheStatsAPI `ADAPTER_RETURNED_EMPTY` correctamente reportado.

---

## 3) Pendientes y siguientes pasos (post-F87.1)

### Pendientes P0 (actual)
- Ninguno para F87.1 (cerrado).

### Pendientes no bloqueantes
- (F84.c) lineups / injuries — fuera de scope inicial, requiere confirmar cobertura TheStatsAPI.
- (F84.d) standings — fuera de scope inicial.
- (P3) Expandir `team_name_translations.py` para clubes UCL/UEL.

### Futuras mejoras recomendadas (global)
- Backtest de la calibración F86.1 con ≥ 30 picks reales con H2H aplicado para ajustar thresholds, `MAX_H2H_POINTS_TOTAL` y el cap DNB.
- Implementar calibrador offline cuando exista una fuente estable (p.ej. `football_market_results`) + endpoint opcional.
- Para FBref Phase 2: ampliar heurísticas (country/team_type) para equipos UCL/UEL.
- Para odds: comparar `bookmakers_count` TSA vs APS como métrica de calidad.

---

## 6) Validación esperada (estado actual)

- Suites actuales:
  - Backend: **2942 passed, 2 skipped**.
  - Frontend: **125 passed**.

- Reglas de operación:
  - Siempre usar `yarn` (no `npm`).
  - Arquitectura fail-soft: no levantar excepción sin convertirla a auditoría/razón.
  - No romper el contrato: discovery adapters deben emitir dicts uniformes (shape API-Football), y el contract debe aceptar múltiples shapes upstream.

- Flags / env relevantes:
  - `ENABLE_THESTATSAPI_FIXTURES_PRIMARY` / `ENABLE_API_FOOTBALL_FALLBACK` / `ENABLE_SOFASCORE_PW_FALLBACK` / `ENABLE_SCRAPEDO_FIXTURES_FALLBACK`.
  - `F87_MIN_VIABLE_COUNT`.
  - `ENABLE_UNKNOWN_COMPETITION_BUCKET` + `UNKNOWN_COMPETITION_HYDRATE_CAP`.
  - ✅ **F87.1:** `DISCOVERY_DROPPED_SAMPLE_CAP` (default `3`).
