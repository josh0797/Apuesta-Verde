# Reporte de Investigación · Tarjetas vs Córners como Features Predictivas

**Fecha**: 2026-06-20 · **Sprint**: D8-Research · **Status**: observe_only, sin implementación

---

## Resumen Ejecutivo (TL;DR)

| Hipótesis | Resultado | Significancia |
|---|---|---|
| Las **tarjetas** predicen Over/Under de goles | ❌ NO | Corr=−0.06 (EPL), ROI hipo Over 4.5 = **−20.46%** |
| Las **tarjetas** se relacionan con córners | ⚠️ Débil | Corr=+0.13 (no actionable) |
| El **DOMINANT_FAVORITE** genera más córners que el equipo inferior | ✅ **SÍ, fuertemente** | **81%** de partidos; diff=4.63 corners; **t=9.68, p≈0** |
| Los **shots totales** son el #1 predictor de córners | ✅ SÍ | Corr=+0.28 (sample), **+0.55 por equipo** |

**Recomendación priorizada**: **Construir el motor de córners primero**. Las tarjetas tienen señal predictiva muy débil y ROI hipotético negativo; los córners tienen señal robusta y mercados con base rates explotables.

---

## Datos utilizados (Opción B aprobada por el usuario)

| Dataset | n | Fuente | Variables |
|---|---|---|---|
| Selecciones (Euro2024 + Copa América 2024) | **44** | 365Scores `/web/game/?` via scrape.do | Cards (yellow+red), 1T/2T split, referee, score |
| EPL 24/25 | **380** | football-data.co.uk E0_2425.csv (gratis) | Cards, fouls, **corners**, shots, shots on target, referee, FTR, odds Pinnacle |

**Gap documentado**: los 64 partidos de WC2022 NO se pudieron recuperar de 365Scores (su endpoint `/allscores/` no retiene data tan antigua). Análisis sin WC2022 → 44 selecciones disponibles, no 123.

---

## PARTE 1 — Backtest de Tarjetas

### 1.1 Estadísticos descriptivos

| Métrica | Selecciones (n=44) | EPL (n=380) |
|---|---|---|
| Total cards/partido (mean) | **5.00** | 4.19 |
| Mediana | 4.0 | 4.0 |
| Stdev | 3.21 | 2.25 |
| P25 / P75 | 3 / 6 | 3 / 6 |
| Min / Max | — | — |
| Home cards (mean) | 2.16 | 1.97 |
| Away cards (mean) | 2.84 | 2.21 |
| Cards 1T (mean) | 1.86 | — (no disponible en CSV) |
| Cards 2T (mean) | 3.14 (= **1.69× el 1T**) | — |

**Observación clave**: en selecciones, **el 2T concentra 63% de las tarjetas** — coherente con cansancio + presión final.

### 1.2 Distribución del total de tarjetas

| Bucket | Selecciones | EPL |
|---|---|---|
| 0-2 | 6 (14%) | ~21% |
| 3-4 | 18 (41%) | ~37% |
| 5-6 | 13 (30%) | ~25% |
| 7-8 | 3 (7%) | ~13% |
| 9+ | 4 (9%) | ~4% |

### 1.3 Preguntas del usuario

**¿Partidos con muchas tarjetas tienen más o menos goles?**
- High cards (≥5, n=155 EPL): **2.89 goles/partido**.
- Low cards (≤2, n=88 EPL): **3.03 goles/partido**.
- Welch t-test: **t=−0.67, df=189.7, p≈0.50** → **NO hay diferencia significativa**.
- Corr(cards_total, goals_total) = **−0.06 EPL, +0.13 selecciones** → cerca de cero en ambos casos.

**¿Existe relación tarjetas ↔ córners?**
- Corr = **+0.13** (débil, no actionable).

**¿Tarjetas mejoran la selección Over/Under goles?**
- Cards by FTR: H=4.08, D=4.62, A=4.01 → **empates tienen +0.6 cards en promedio**, pero la diferencia no se traduce en edge contra el mercado.

**¿Umbral útil 4.5 tarjetas?**
- Hit rate Over 4.5: **40.79%**.
- ROI hipotético a cuota 1.95: **−20.46%** (no rentable).
- Hit rates: Over 3.5 = 60%, Over 4.5 = 41%, Over 5.5 = 28%.

### 1.4 Veredicto PARTE 1

⚠️ **Las tarjetas NO añaden valor predictivo significativo** para:
- Over/Under goles (cards-goals NO correlacionan).
- Mercado Over X.5 cards (ROI hipotético negativo).
- Resultado 1X2 (diferencia entre empate y victoria solo 0.6 cards).

La señal "el árbitro estricto genera más cards" (ya validado en Sprint-D8/E PASO 2) tiene AUC=0.55-0.59 y ya está cerrada en la rubric de no avance.

---

## PARTE 2 — Backtest de Córners (EPL 380)

### 2.1 Estadísticos descriptivos

| Métrica | Valor |
|---|---|
| Total corners/partido (mean) | **10.30** |
| Mediana | 10.0 |
| Stdev | 3.47 |
| P25 / P75 | 8 / 13 |
| Home corners (mean) | 5.43 |
| Away corners (mean) | 4.87 |
| Signed diff (H−A) | +0.55 (ventaja localía pequeña) |

### 2.2 Base rates Over/Under y ROI hipotético

| Línea | Over rate | Hypothetical ROI Over @1.85 | Under rate | Hypothetical ROI Under @1.85 |
|---|---|---|---|---|
| 5.5 | **91.6%** | **+69.4%** ⚠️ (cuotas reales serán <1.85) | 8.4% | −84.4% |
| 6.5 | 85.8% | +58.7% | 14.2% | −73.7% |
| 7.5 | 76.3% | **+41.2%** | 23.7% | −56.2% |
| 8.5 | 70.5% | **+30.5%** | 29.5% | −45.5% |
| **9.5** | **59.7%** | **+10.5%** ⭐ | 40.3% | −25.5% |
| 10.5 | 48.4% | −10.4% | 51.6% | −4.6% |
| 11.5 | 32.9% | −39.1% | 67.1% | **+24.1%** |

> **Cuidado interpretativo**: los ROI a 1.85 son optimistas. En la práctica los bookmakers ajustan las cuotas hacia el equilibrio (en líneas bajas, Over 5.5 paga ~1.10 no 1.85). El insight robusto es la **base rate**, no el ROI nominal.
>
> El **sweet spot real** está en líneas **9.5/10.5** donde las cuotas son ~1.85-2.00 y la base rate ofrece margen para extraer edge si el modelo discrimina mejor que el cierre.

### 2.3 Correlaciones del total de córners

| Variable | Correlación con total corners | Magnitud |
|---|---|---|
| **shots_total** | **+0.277** | 🔥 mejor predictor del conjunto |
| shots_on_target | +0.157 | Moderada |
| cards_total | +0.130 | Débil |
| **home_corners ↔ home_shots** | **+0.543** | 🔥🔥 fuerte |
| **away_corners ↔ away_shots** | **+0.550** | 🔥🔥 fuerte |
| fouls_total | −0.074 | Insignificante |
| goals_total | −0.051 | Insignificante |

**Insight**: los **shots por equipo** son el predictor más fuerte de los **corners de ese equipo** (corr ≈ 0.55). El total de córners es menos predecible que la *split* por equipo.

---

## PARTE 3 — Validación de la Hipótesis Central

> **¿El DOMINANT_FAVORITE genera significativamente más córners que el equipo inferior?**

### 3.1 Definición operativa (anti-overfitting)

- **DOMINANT_FAVORITE**: equipo con `implied_prob_devig ≥ 0.65` en el mercado h2h de Pinnacle pre-partido.
- Solo features prematch (cuotas) — sin leakage.

### 3.2 Resultados sobre EPL 24/25

| Métrica | Valor |
|---|---|
| n DOMINANT_FAVORITE matches | **90** (68 local + 22 visitante) |
| n balanced (sin DF claro) | 290 |
| **% partidos donde el favorito tuvo MÁS córners** | **81.11%** ✅ |
| % donde el favorito tuvo MENOS córners | 15.56% |
| % iguales | 3.33% |
| Favorito · mean corners | **7.78** (p25/p75 = 4/10) |
| Underdog · mean corners | **3.14** (p25/p75 = 1/4) |
| **Diferencia promedio (fav − dog)** | **+4.63 córners** |
| **Bootstrap 95% CI sobre la diferencia** | **[3.54, 5.69]** (no incluye 0) |
| **Welch t-test (fav vs dog corners)** | **t=9.68, df=143.7, p≈0.00000** |

### 3.3 Veredicto

✅ **HIPÓTESIS CONFIRMADA con altísima significancia estadística**.

El favorito dominante genera en promedio **2.5× más córners** que su rival inferior. La diferencia (~5 córners) es enorme — equivale a media línea entera del mercado total. Este es el **principal hallazgo del estudio**.

### 3.4 Ranking de feature importance (|corr| con total corners)

| Rank | Feature | Correlación |
|---|---|---|
| 1 | shots_total | +0.277 |
| 2 | shots_on_target | +0.157 |
| 3 | cards_total | +0.130 |
| 4 | abs_implied_prob_diff (favorito gap) | +0.090 |
| 5 | fouls_total | −0.074 |
| 6 | home_implied_prob | +0.051 |
| 7 | goals_total | −0.051 |

**Cards** ocupa el rank #3 pero con magnitud muy baja (0.13). El edge real está en **shots** (rank 1-2) y **favorito gap** (rank 4).

---

## PARTE 4 — Evaluación Técnica de football-data.co.uk como Fuente de Córners

### 4.1 Cobertura y datos ofrecidos

| Dimensión | football-data.co.uk |
|---|---|
| **Córners** | ✅ HC (home), AC (away) por partido FT |
| **Tarjetas** | ✅ HY/AY/HR/AR |
| **Faltas** | ✅ HF/AF |
| Tiros / SoT | ✅ HS/HST, AS/AST |
| Goles FT + HT | ✅ FTHG/FTAG + HTHG/HTAG |
| Resultado 1X2 | ✅ FTR |
| Referee | ✅ Referee |
| **Odds históricas** | ✅ 30+ bookmakers (Pinnacle/Bet365/etc.) para 1X2, AH, OU 2.5 |
| Goles minuto a minuto | ❌ |
| Eventos por minuto | ❌ |
| xG | ❌ (a veces, en algunas temporadas) |

### 4.2 Competiciones y cobertura

- **Top-5 europeas**: EPL (E0), La Liga (SP1), Bundesliga (D1), Serie A (I1), Ligue 1 (F1).
- **Tier-2**: Championship (E1), 2. Bundesliga, Segunda, etc.
- **Histórico**: desde **1993/94** para EPL (la cobertura de córners empezó en torno a 2000/01).
- **NO cubre**: torneos internacionales (WC, Euro, Copa América), Champions League, copas nacionales.
- **Actualización**: **diaria** post-partido (típicamente 6-12h después del final).
- **Calidad**: muy alta. Es una fuente "de referencia" usada por bookmakers para back-testing.

### 4.3 Limitaciones para nuestro caso

1. **Solo ligas top** — inútil para selecciones (que es donde el motor actual hace picks).
2. **Sin granularidad temporal** — no se sabe en qué minuto ocurrieron los córners.
3. **CSV anual** — no es streaming; hay que re-descargar el archivo de la temporada.

### 4.4 Comparativa contra otras fuentes

| Fuente | Cubre córners | Granularidad temporal | Cobertura | Licencia | Latencia | Costo |
|---|---|---|---|---|---|---|
| **football-data.co.uk** | ✅ totales FT | ❌ solo agregado | Ligas top-5 + tier-2 | ✅ Free (con atribución) | Post-partido 6-12h | Free |
| **TheStatsAPI** (vía scrape.do) | ⚠️ parcial — depende de competición | ✅ live event-by-event | Global | Comercial (depende del provider) | Live | scrape.do credits |
| **Football-Data.org** | ❌ No publica corners en plan free | n/a | Top competitions | Tiered (free / paid) | n/a | n/a corners |
| **API-Football** (api_sports) | ✅ totales + a veces por equipo | ⚠️ depende del plan | Global, 1100+ ligas | Comercial | Live | Plan pagado |
| **365Scores** (`/game/stats/`) | ✅ cuando funciona | ⚠️ solo agregado | Global | Sin contrato; via scrape.do | Post-partido | scrape.do credits |
| **TheSportsDB** | ❌ No expone corners de forma confiable | n/a | Global; metadata fixtures | Free (key required) | Variable | Free |

### 4.5 Veredicto técnico

✅ **football-data.co.uk es ÓPTIMO como fuente para CONSTRUIR Y CALIBRAR el motor de córners** en ligas (380 partidos/liga/temporada × 5 ligas × 5+ temporadas = ~10,000+ partidos con corners completos y odds). Pero **NO sirve para producción live** (latencia 6-12h) ni para selecciones.

**Estrategia recomendada**:
- **Training/calibración**: football-data.co.uk (free, masivo, limpio).
- **Producción live**: API-Football (api_sports) — única fuente integrada que entrega córners post-partido en horas razonables con cobertura amplia.
- **Fallback live**: TheStatsAPI con extractor de córners (probado en Sprint F82).

---

## PARTE 5 — Propuesta de Arquitectura del Motor de Córners

### 5.1 Mercados objetivo

| Tipo | Líneas |
|---|---|
| **Total corners O/U** | 5.5, 6.5, 7.5, 8.5, 9.5, 10.5, 11.5 |
| **Most corners** (mercado binario H/A) | Home / Away / Tie |
| **Team handicap corners** | Home −1.5, Home −2.5, Away −1.5, Away −2.5 |
| **Team Over X corners** | Equipo A Over 3.5/4.5/5.5, idem equipo B |
| **Ambos equipos 3+ córners** | binario |

### 5.2 Variables (features prematch) necesarias

| Feature | Disponibilidad actual | Importancia esperada |
|---|---|---|
| Average shots last N (por equipo) | ✅ derivable de histórico | 🔥🔥 (rank #1) |
| Average corners for / against last N (por equipo) | ⚠️ requiere fuente con corners | 🔥🔥 |
| Implied probability gap (h2h devig) | ✅ ya calculado | 🔥 (rank #4) |
| Home/away advantage (intercept) | ✅ baseline +0.55 corners | 🔥 |
| Tactical style (parking-the-bus vs attacking) | ⚠️ proxy: shots received avg | 🔥 |
| Referee (no relevante) | ✅ pero ignorar | ⚪ |

### 5.3 Modelo estadístico recomendado

**Bivariate Poisson** (mismo paradigma usado para Over/Under goles):
- λ_home_corners = f(home_shots_avg, away_shots_received_avg, home_advantage, fav_gap)
- λ_away_corners = f(away_shots_avg, home_shots_received_avg, fav_gap)
- Total corners = sum; mercados team-level y diferenciales se derivan de la distribución conjunta.

**Pesos sugeridos (a calibrar con CV en EPL 24/25)**:
- `w_shots = 0.55` (señal dominante)
- `w_team_corner_history = 0.30`
- `w_fav_gap = 0.10`
- `w_home_advantage = 0.05` (intercept)

### 5.4 Nuevas features candidatas (PARTE 3 del brief)

| Feature | Definición | Correlación esperada con corners totales | Riesgo overfit |
|---|---|---|---|
| **Corner Dominance Index** | (home_corners_for_avg − home_corners_against_avg) | Medio-alto | Bajo (es un season-avg) |
| **Corner Differential Expected** | λ_home − λ_away del modelo Poisson | Alto (es la predicción) | Medio (depende de pesos) |
| **Team Corner Strength** | corners_for_avg / liga_avg | Alto | Bajo |
| **Opponent Corner Weakness** | corners_against_avg del oponente / liga_avg | Alto | Bajo |
| **Favorite Corner Advantage** | implied_prob_diff × team_corner_strength_gap | **Muy alto** (sintetiza rank #1 y #4) | Medio |
| **Corner Pace** | (corners_for + corners_against)_last_5 / 5 | Medio | Bajo |
| **Corner Pressure Score** | shots_received_last_5 × possession_against_last_5 | Medio | Alto si no se controla por liga |

**Top 3 que YO implementaría primero**: `Team Corner Strength`, `Opponent Corner Weakness`, `Favorite Corner Advantage` — todos derivables del histórico de football-data.co.uk sin coste.

### 5.5 Datos que ya existen vs faltantes

| Necesario | Ya tenemos | Cómo conseguir lo faltante |
|---|---|---|
| Corners históricos por partido (top-5 ligas) | ✅ football-data.co.uk | n/a |
| Shots por equipo histórico | ✅ football-data.co.uk (HS/AS) | n/a |
| Possession % por partido | ❌ | API-Football live; o calcular proxy via shots ratio |
| xG por partido | ❌ | StatsBomb (paid) / fbref (scrape) / API-Football |
| Cuotas históricas de córners O/U | ⚠️ The Odds API (paid credits) | The Odds API histórico |
| Cuotas live de córners | ⚠️ The Odds API | mismo |

### 5.6 Complejidad estimada

| Componente | LOC estimadas | Esfuerzo |
|---|---|---|
| Feature builder (`football_corners_features.py`) | 200 | 2h |
| Bivariate Poisson predictor (`football_corners_predictor.py`) | 250 | 3h |
| Backtest CLI + calibration vs market devig (cuando haya odds) | 200 | 2h |
| Tests (in_range, monotonic, PIT no-leakage, calibration) | 300 | 2h |
| Reglas de gating / hard rules / UI tile | 200 | 2h |
| **Total** | **~1150 LOC** | **~11h** |

---

## Recomendaciones Priorizadas

### Prioridad 1 (P0): Construir el motor de córners
- **Por qué**: hipótesis DOMINANT_FAVORITE→corners CONFIRMADA con p≈0 y t=9.68 sobre n=90 — el insight más fuerte del estudio.
- **Cuándo**: ya. Datos disponibles gratis (football-data.co.uk) para calibrar.
- **Líneas objetivo iniciales**: O/U 9.5 (base rate 60%) y O/U 10.5 (base rate 48%) — donde las cuotas son ~1.85 y el edge potencial es real.
- **Validación esperada**: AUC ≥ 0.65 vs hits binarios; delta_brier_vs_devig < 0; cohorte DOMINANT_FAVORITE con ROI CI_low > 0.

### Prioridad 2 (P1): Investigar si el endpoint stats de 365Scores tiene alternativa
- **Por qué**: la integración live de córners en producción depende de tener un proveedor en horas, no días.
- Tarea concreta: probar `?includeStatistics=true` en `/game/?` o el path `/web/games/stats/`, y como fallback definitivo evaluar el plan pagado de API-Football.

### Prioridad 3 (P2): NO implementar el predictor de cards
- **Por qué**: cards no añaden valor predictivo sobre goles, no tienen ROI hipo en su propio mercado, y el factor árbitro ya fue cerrado en Sprint-D8/E PASO 2 con AUC 0.55-0.59 (no robusto).
- **Acción**: archivar la idea como "estudiada y refutada"; reusar el ingestor PIT que ya construimos por si se necesita en futuro.

### Prioridad 4 (P3): Posponer features avanzadas hasta calibrar el motor base
- Corner Pace, Corner Pressure Score, etc. son refinamientos. Implementarlas antes del modelo base es premature optimization.

---

## Apéndice · Listado de Stats RAW

Disponibles en `/app/diagnostics/sprint_d8_cards_corners_research_stats.json`.

**Créditos consumidos en esta investigación**: ~93 scrape.do (44 selecciones cards) + 0 The Odds API + 0 API-Football.
