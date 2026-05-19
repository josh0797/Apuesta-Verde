# plan.md — Value Bet Intelligence

## 1) Objectives
- Prove the **core workflow** works end-to-end with real data: fetch fixtures/odds/context → normalize into 3-layer schema → LLM produces **strict, risk-managed value picks** (max 3–5/day) or “Hoy no hay valor…”.
- Build an MVP web app (dark sportsbook UI, ES/EN) to browse matches, see picks, and track results.
- Add free login (Google OAuth via Emergent) after core + v1 are stable.

---

## 2) Implementation Steps

### Phase 1 — Core POC (isolation, do not proceed until green)
**Goal:** `/app/poc/test_core.py` validates API-Football + fallback scraping + LLM JSON output.

1) **Web research (best practices & gotchas)**
   - Check API-Football v3 endpoints for fixtures/odds/team stats/standings + rate limits.
   - Validate scraping targets feasibility (static HTML vs JS-rendered) and pick 1 reliable scoreboard page for fallback smoke test.

2) **API-Football client + sampling**
   - Implement `httpx` client with headers `x-apisports-key`.
   - Fetch:
     - Upcoming fixtures (next 48h; status NS).
     - Live fixtures (LIVE/1H/HT/2H).
     - Odds for a sample fixture (multi-bookmaker markets).
     - Team stats / last matches for form.
     - Standings for motivation context.

3) **Normalize to 3-layer schema**
   - Build minimal transformers: `odds_snapshots`, `team_context`, `live_stats` with timestamps.
   - Add freshness checks (+ penalties flags) exactly per spec.

4) **LLM analysis pipeline (Emergent Universal Key, Claude Sonnet 4.5)**
   - Encode the full analyst persona as **system prompt**.
   - Provide strict JSON schema for response (picks array + final summary + freshness).
   - Parse/validate JSON; enforce hard constraints (allowed markets only, confidence ≥68, max 3–5 recs).

5) **Fallback chain smoke test**
   - Simulate API failure; run a minimal web fetch + HTML parse against a public scoreboard (e.g., ESPN) to confirm fallback triggers.
   - Output “fallback_used=true” and return minimal match list (even if no odds).

6) **POC acceptance loop**
   - Iterate until script consistently produces:
     - ≥1 valid pick JSON **or** explicit “Hoy no hay valor…”
     - With motivation scoring, risks, and data freshness.

**Phase 1 user stories**
1. As a user, I want the system to fetch real fixtures for the next 48 hours so analysis is actionable.
2. As a user, I want the system to fetch multi-bookmaker odds snapshots so it can detect line movement.
3. As a user, I want the system to label motivational urgency (1–5) before recommending any bet.
4. As a user, I want the system to return strict structured JSON so the UI can render reliably.
5. As a user, I want the system to explicitly say “Hoy no hay valor…” when nothing meets criteria.

---

### Phase 2 — V1 App Development (MVP around proven core; auth delayed)
**Goal:** Working app without login: dashboard + match detail + history (single-tenant for now).

1) **Backend (FastAPI + MongoDB/Motor)**
   - Services:
     - `data_ingestion` (API-Football + cache collections for odds/context/live).
     - `analyst_engine` (calls LLM with system prompt, stores picks).
   - Endpoints (no auth yet):
     - `GET /api/matches/upcoming`, `GET /api/matches/live`, `GET /api/matches/{id}`
     - `POST /api/analysis/run` (generate picks)
     - `GET /api/picks/today`, `GET /api/picks/history`
     - `POST /api/picks/{id}/track` (won/lost/push)
     - `GET /api/stats/dashboard`

2) **Frontend (React + Tailwind + shadcn/ui)**
   - Dark sportsbook theme + ES/EN toggle.
   - Pages:
     - `/` Dashboard (picks grouped: Alta/Media/Descartados/Datos incompletos)
     - `/live` Live view (auto-refresh optional)
     - `/match/:id` 3-layer data + analyst output
     - `/history` tracking table + basic accuracy chart
   - Components: PickCard, MotivationBadge, DataFreshnessIndicator, OddsTable (movement), ConfidenceMeter.

3) **Incremental E2E test**
   - Run v1: ingest → analyze → render picks → track outcome.
   - Fix broken states: no data, stale data, empty picks, parsing errors.

**Phase 2 user stories**
1. As a user, I want to see today’s picks grouped by confidence so I can decide quickly.
2. As a user, I want to open a match and see motivation + key risks so I understand why it’s recommended.
3. As a user, I want to see data freshness warnings so I can avoid stale-based bets.
4. As a user, I want to mark picks as win/loss/push so I can track accuracy.
5. As a user, I want ES/EN UI toggle so I can use the app bilingually.

---

### Phase 3 — Add Auth + Multi-user + Hardening
**Goal:** Free login day-1 requirement implemented after core stability.

1) **Emergent Google Auth integration**
   - Add auth endpoints `/api/auth/session`, `/api/auth/me`, `/api/auth/logout`.
   - Add user scoping on picks & tracking; migrate history to per-user.

2) **Scheduler + refresh strategy (MVP)**
   - Add background jobs (or timed endpoints) for:
     - odds snapshots every 30 min
     - team context every 6h
   - Ensure “stale” detection stays consistent.

3) **Scraping fallback expansion (MVP-hardening)**
   - Add 1–2 additional sources (Sofascore/Flashscore) where static HTML works.
   - Persist fallback provenance + partial data flags.

4) **Testing pass**
   - Auth flow + data isolation + core flow remains green.

**Phase 3 user stories**
1. As a user, I want to log in with Google so my pick history is saved.
2. As a user, I want my tracked results to be private to my account.
3. As a user, I want picks to refresh automatically so I don’t rely on outdated odds.
4. As a user, I want the app to keep working when API-Football fails by using fallback sources.
5. As a user, I want the dashboard to clearly show when picks were generated and with what data freshness.

---

### Phase 4 — Polish (post-MVP)
- Alerts for new high-confidence picks.
- Filters (league/market/confidence), CSV export.
- Better performance stats (ROI placeholder, streaks) + charts.

**Phase 4 user stories**
1. As a user, I want alerts when a new high-confidence pick appears so I don’t miss value.
2. As a user, I want to filter by leagues/markets so I can focus on my preferences.
3. As a user, I want to export picks to CSV so I can analyze externally.
4. As a user, I want richer stats so I can evaluate long-term performance.
5. As a user, I want the UI to stay fast even with many matches/picks.

---

## 3) Next Actions (immediate)
1. Create `/app/poc/test_core.py` and run it against API-Football with your key.
2. Validate at least one odds market mapping (1X2 + Under lines) into `odds_snapshots`.
3. Implement the LLM prompt + JSON schema + parser; iterate until output passes validation.
4. Add fallback smoke test (ESPN scoreboard fetch + parse) and force-failure toggle.

---

## 4) Success Criteria
- **POC:** Script reliably outputs valid structured JSON (picks or “Hoy no hay valor…”) with motivation scoring, risk flags, allowed markets only, confidence computed + freshness penalties.
- **V1 App:** Dashboard renders picks, match detail shows 3 layers + reasoning/risks, history tracking works, ES/EN toggle works.
- **Auth Phase:** Google login works; data is user-scoped; core workflow remains stable.
- **Resilience:** API-Football failure triggers fallback path and app remains usable with partial-data warnings.
