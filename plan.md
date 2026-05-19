# plan.md — Value Bet Intelligence (Updated)

## 1) Objectives
- ✅ **Core workflow proven end-to-end** with real data: fixtures/odds/context → normalized 3-layer schema → LLM produces **strict, risk-managed value picks** (max 3–5/day) or “Hoy no hay valor…”.
- ✅ **MVP app delivered** with dark sportsbook-modern UI, bilingual ES/EN, match detail transparency, and pick tracking.
- ✅ **Authentication included from day 1** (implemented as free email+password + JWT; demo user seeded).
- 🔁 **Operational objective (current focus):** keep the system reliably generating picks despite:
  - API-Football free plan constraints (10 req/min + season access limits)
  - Emergent LLM key credit/budget constraints

---

## 2) Implementation Steps

### Phase 1 — Core POC (isolation, do not proceed until green)
**Goal:** `/app/poc/test_core.py` validates API-Football + fallback scraping + LLM JSON output.

✅ **Status: COMPLETE**

Completed items:
1) **API-Football client + sampling**
   - Fetch fixtures (next 48h) and live fixtures.
   - Fetch odds for sample fixtures.
   - Fetch team context where available.

2) **Normalize to 3-layer schema**
   - `odds_snapshots`, `team_context`, `live_stats`.
   - Data freshness flags and penalties support.

3) **LLM analysis pipeline (Emergent Universal Key, Claude Sonnet 4.5)**
   - Full analyst persona implemented.
   - Strict JSON output parsed/validated.

4) **Fallback chain smoke test**
   - ESPN public scoreboard fallback verified.

5) **POC acceptance loop**
   - All 8 critical checks passed.

**Phase 1 user stories** (✅ validated)
1. Fetch real fixtures for next 48 hours.
2. Fetch multi-bookmaker odds snapshots.
3. Label motivational urgency (1–5).
4. Return strict structured JSON.
5. Return explicit “Hoy no hay valor…” when applicable.

---

### Phase 2 — V1 App Development (MVP around proven core; auth included)
**Goal:** Working app **with login**, dashboard + match detail + history, plus tracking.

✅ **Status: COMPLETE**

#### 2.1 Backend (FastAPI + MongoDB/Motor)
Implemented:
- `/app/backend/server.py`
- `/app/backend/services/`
  - `api_football.py`:
    - **Token bucket rate limiter** (~8 req/min to stay below 10/min free plan)
    - **Mongo cache**:
      - Odds cache TTL: ~30 minutes
      - Context cache TTL: ~6 hours (team_stats, standings, injuries, H2H)
    - Uses **proxy season 2024** when current season access is blocked by plan.
  - `data_ingestion.py`:
    - Top-league prioritization
    - Serial enrichment to respect rate limits
    - Optional deep-enrichment for top candidates
  - `analyst_engine.py`:
    - Claude Sonnet 4.5 via Emergent Universal LLM key
    - Full Spanish analyst persona prompt
    - Strict JSON parsing
    - Updated prompt guidance to treat proxy-season context as usable (not auto-incomplete)
  - `normalizer.py`: 3-layer schema normalization
  - `fallback_scraper.py`: ESPN fallback
  - `auth.py`: JWT email+password auth + demo seed user

Endpoints delivered (auth-protected unless noted):
- Public:
  - `GET /api/` health
- Auth:
  - `POST /api/auth/register`
  - `POST /api/auth/login`
  - `GET /api/auth/me`
  - `POST /api/auth/logout`
  - `PATCH /api/auth/me/language`
- Matches:
  - `GET /api/matches/upcoming?refresh=bool`
  - `GET /api/matches/live?refresh=bool`
  - `GET /api/matches/{match_id}`
- Analysis:
  - `POST /api/analysis/run`
- Picks:
  - `GET /api/picks/today`
  - `GET /api/picks/history`
  - `GET /api/picks/run/{run_id}`
  - `POST /api/picks/track`
  - `GET /api/picks/tracked`
- Stats:
  - `GET /api/stats/dashboard`

#### 2.2 Frontend (React + Tailwind + shadcn/ui)
✅ Dark sportsbook-modern theme applied using tokens from `design_guidelines.md`.
✅ ES/EN language toggle in header.
✅ Pages implemented:
- `/login` (premium login, demo login)
- `/` dashboard (picks grouped + summary)
- `/live` live matches
- `/match/:id` match detail (odds comparison + analysis + track buttons)
- `/history` tracked picks + KPIs
- `/profile` user profile + stats

Key UI components implemented:
- Confidence meter, motivation badge, freshness badges
- Odds comparison table (best price highlight)
- Risk chips and cash-out indicator
- Framer-motion micro-animations

#### 2.3 Testing
✅ Backend testing via `testing_agent_v3`: **19/20 tests passed**.
- Only failure: **Emergent LLM key budget exceeded** during one run (billing/credits issue, not a code bug).
- LLM integration verified working via multiple successful analysis runs.

**Phase 2 user stories** (✅ delivered)
1. See picks grouped by confidence.
2. Match detail shows motivation + risks.
3. Data freshness warnings visible.
4. Mark picks as won/lost/push and track accuracy.
5. ES/EN UI toggle.

---

### Phase 3 — Operational Hardening + Optional Enhancements (Not started)
**Goal:** Improve reliability, automation, and breadth of fallback sources.

🔲 **Status: NOT STARTED**

1) **Scheduler / refresh strategy**
- Add background jobs to refresh:
  - odds snapshots every 30 minutes
  - team context every 6 hours
- Store refresh metadata and show “last refresh” in UI.

2) **Fallback expansion (web search + scraping)**
- Add additional sources as fallback layers:
  - Sofascore, Flashscore, SportyTrader, ESPN
- Persist provenance (`source`, `fallback_used`, `partial_data`) and display in UI.

3) **User-facing filters & workflow improvements**
- Filters by league / market / confidence
- Saved preferences per user

4) **Export + reporting**
- CSV export of picks and tracking
- Basic ROI placeholder improvements

5) **Auth enhancement (optional)**
- If required: replace/extend JWT auth with Emergent Google OAuth.

**Phase 3 user stories**
1. Picks refresh automatically without manual action.
2. App continues to function when API-Football fails via fallback.
3. Users can filter to preferred leagues/markets.
4. Users can export data for external analysis.
5. Optionally: Google login for faster onboarding.

---

### Phase 4 — Polish (post-MVP)
🔲 **Status: NOT STARTED**
- Alerts for new high-confidence picks.
- Advanced filters & saved views.
- Richer stats dashboard (ROI, streaks, breakdown by market/league).
- Performance enhancements (virtualized tables if needed).

**Phase 4 user stories**
1. Alerts when new high-confidence pick appears.
2. League/market filters for fast scanning.
3. Export to CSV.
4. Rich performance stats.
5. UI remains fast with many matches/picks.

---

## 3) Next Actions (immediate)
1) **LLM credits / budget**
   - Ensure Emergent LLM key has sufficient credits to keep generating new picks.
   - If budget exceeded: top up credits or replace with another key.

2) **Stability improvements (recommended)**
   - Keep `analysis/run` using `refresh:false` by default (cache-first) to avoid rate-limit churn.
   - Consider reducing analysis match count and/or deep-enrichment count depending on observed API limits.

3) **Start Phase 3 if desired**
   - Add scheduler for periodic refresh.
   - Add additional fallback scrapers.
   - Add filters + export.

---

## 4) Success Criteria
- ✅ **POC:** Strict JSON picks/no-value output with motivation + risk + freshness.
- ✅ **MVP App:** Dashboard + match detail + tracking + history + ES/EN + dark theme.
- ✅ **Auth:** Free login available from day 1 (JWT email/password) + demo user.
- ✅ **Resilience:** API-Football rate limits handled with token bucket + Mongo caching; ESPN fallback available.
- 🔁 **Operational:** LLM credits maintained so pick generation remains available for end users.
