"""Sprint F.1 — tests for ``three65scores_identity_resolver``.

Strict rules
------------
* Fixture IDs (``5106``/``2383``/``4627854``/``5930``) only appear in
  this file. Production code stays free of hard-coded test IDs.
* Every test injects ``games_fetcher`` / ``game_detail_fetcher``;
  there is **no** real HTTP call.
* The Mongo path uses a minimal in-memory FakeDB.

The reference match is Mexico vs South Korea, FIFA World Cup 2026
group stage, 2026-06-17 (synthetic kickoff for the test).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest

from services.external_sources import three65scores_identity_resolver as resolver


# ════════════════════════════════════════════════════════════════════════
# Fixture constants — TEST ONLY
# ════════════════════════════════════════════════════════════════════════
FIX_GAME_ID         = 4627854
FIX_COMPETITION_ID  = 5930
FIX_HOME_TEAM_ID    = 5106
FIX_AWAY_TEAM_ID    = 2383
FIX_INTERNAL_ID     = "test:365scores:game:4627854"
FIX_KICKOFF         = datetime(2026, 6, 17, 22, 0, 0, tzinfo=timezone.utc)
FIX_HOME_NAME_SRC   = "Mexico"
FIX_AWAY_NAME_SRC   = "South Korea"

FIX_MATCH_URL = (
    "https://www.365scores.com/football/match/"
    "fifa-world-cup-5930/mexico-south-korea-"
    f"{FIX_AWAY_TEAM_ID}-{FIX_HOME_TEAM_ID}-{FIX_GAME_ID}"
)


def _game_doc(
    *,
    game_id: int = FIX_GAME_ID,
    competition_id: int = FIX_COMPETITION_ID,
    home_id: int = FIX_HOME_TEAM_ID,
    away_id: int = FIX_AWAY_TEAM_ID,
    home_name: str = FIX_HOME_NAME_SRC,
    away_name: str = FIX_AWAY_NAME_SRC,
    kickoff: datetime = FIX_KICKOFF,
    swap_competitors_order: bool = False,
    use_is_home_flag: bool = False,
) -> dict:
    """Build a 365Scores-like game dict for tests."""
    home_comp = {"id": home_id, "name": home_name, "symbolicName": home_name}
    away_comp = {"id": away_id, "name": away_name, "symbolicName": away_name}
    if use_is_home_flag:
        home_comp["isHome"] = True
        away_comp["isHome"] = False
    if swap_competitors_order:
        competitors = [away_comp, home_comp]
    else:
        competitors = [home_comp, away_comp]
    return {
        "id":             game_id,
        "competitionId":  competition_id,
        "startTime":      kickoff.isoformat(),
        "competitors":    competitors,
    }


# ════════════════════════════════════════════════════════════════════════
# Fake Mongo
# ════════════════════════════════════════════════════════════════════════
class FakeCollection:
    def __init__(self):
        self.docs: list[dict] = []
        self.indexes_created: list[tuple[Any, dict]] = []

    async def create_index(self, key, **kwargs):
        self.indexes_created.append((key, kwargs))
        return kwargs.get("name") or "ix"

    async def find_one(self, query):
        for d in self.docs:
            ok = True
            for k, v in (query or {}).items():
                if d.get(k) != v:
                    ok = False
                    break
            if ok:
                return d
        return None

    async def update_one(self, query, update, upsert=False):
        for i, d in enumerate(self.docs):
            ok = True
            for k, v in (query or {}).items():
                if d.get(k) != v:
                    ok = False
                    break
            if ok:
                self.docs[i] = {**d, **(update.get("$set") or {})}
                return {"matched": 1, "modified": 1}
        if upsert:
            new_doc = dict(query or {})
            new_doc.update(update.get("$set") or {})
            self.docs.append(new_doc)
        return {"matched": 0, "modified": 0, "upserted": int(upsert)}

    async def insert_one(self, doc):
        self.docs.append(dict(doc))


class FakeDB:
    def __init__(self):
        self.football_365scores_identities = FakeCollection()


# ════════════════════════════════════════════════════════════════════════
# Pure helpers — alias map / normalisation / validation
# ════════════════════════════════════════════════════════════════════════
class TestNormalisation:
    def test_normalize_strips_accents_and_suffixes(self):
        assert resolver.normalize_team_name("México") == "mexico"
        assert resolver.normalize_team_name("Mexico National Football Team") == "mexico"
        assert resolver.normalize_team_name("FC Barcelona") == "barcelona"

    def test_alias_set_mexico_includes_spanish_form(self):
        s = resolver.build_team_alias_set("Mexico")
        assert "mexico" in s
        assert "méxico" in s or "mexico" in s  # both normalise the same

    def test_alias_set_south_korea_includes_corea_del_sur(self):
        s = resolver.build_team_alias_set("South Korea")
        assert "south korea" in s
        assert "corea del sur" in s
        assert "korea republic" in s

    def test_alias_set_congo_dr_includes_rd_congo(self):
        s = resolver.build_team_alias_set("Congo DR")
        assert "rd congo" in s
        assert "dr congo" in s
        # Republic of the Congo must NOT be confused with Congo DR.
        s2 = resolver.build_team_alias_set("Congo")
        assert "rd congo" not in s2

    def test_alias_set_ivory_coast_includes_cote_d_ivoire(self):
        s = resolver.build_team_alias_set("Côte d'Ivoire")
        assert "ivory coast" in s
        assert "costa de marfil" in s


class TestValidateTeamMapping:
    def test_aligned_when_source_matches_canonical_order(self):
        out = resolver.validate_team_mapping(
            canonical_home="Mexico", canonical_away="South Korea",
            source_home_name="México", source_away_name="Corea del Sur",
        )
        assert out["aligned"] is True
        assert out["swapped"] is False
        assert out["valid"] is True
        assert out["reason"] == resolver.RC_TEAM_MAPPING_OK

    def test_swapped_when_source_inverted(self):
        out = resolver.validate_team_mapping(
            canonical_home="Mexico", canonical_away="South Korea",
            source_home_name="Corea del Sur", source_away_name="México",
        )
        assert out["aligned"] is False
        assert out["swapped"] is True
        assert out["valid"] is True
        assert out["reason"] == resolver.RC_TEAM_MAPPING_SWAPPED

    def test_invalid_when_neither_side_matches(self):
        out = resolver.validate_team_mapping(
            canonical_home="Mexico", canonical_away="South Korea",
            source_home_name="Brazil", source_away_name="Argentina",
        )
        assert out["valid"] is False
        assert out["reason"] == resolver.RC_TEAM_MAPPING_INVALID

    def test_invalid_when_only_one_side_matches(self):
        # One side correct, the other unknown.
        out = resolver.validate_team_mapping(
            canonical_home="Mexico", canonical_away="South Korea",
            source_home_name="México", source_away_name="Brazil",
        )
        assert out["valid"] is False


# ════════════════════════════════════════════════════════════════════════
# Public entry — input guards
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
class TestInputGuards:
    async def test_missing_internal_match_id_returns_not_found(self):
        out = await resolver.resolve_match_identity(
            internal_match_id="",
            home_team="Mexico", away_team="South Korea",
            commence_time=FIX_KICKOFF,
        )
        assert out["status"] == resolver.STATUS_NOT_FOUND
        assert out["reason_code"] == resolver.RC_NO_INPUTS

    async def test_missing_team_returns_not_found(self):
        out = await resolver.resolve_match_identity(
            internal_match_id=FIX_INTERNAL_ID,
            home_team="", away_team="South Korea",
            commence_time=FIX_KICKOFF,
        )
        assert out["status"] == resolver.STATUS_NOT_FOUND

    async def test_no_fetcher_no_url_returns_source_unavailable(self):
        out = await resolver.resolve_match_identity(
            internal_match_id=FIX_INTERNAL_ID,
            home_team="Mexico", away_team="South Korea",
            commence_time=FIX_KICKOFF,
            persist=False,
        )
        assert out["status"] == resolver.STATUS_SOURCE_UNAVAILABLE
        assert out["reason_code"] == resolver.RC_SOURCE_TIMEOUT


# ════════════════════════════════════════════════════════════════════════
# URL path
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
class TestUrlPath:
    async def test_url_path_resolves_high_confidence(self):
        async def detail_fetcher(game_id: str):
            assert game_id == str(FIX_GAME_ID)
            return {"game": _game_doc()}

        out = await resolver.resolve_match_identity(
            internal_match_id=FIX_INTERNAL_ID,
            home_team="Mexico", away_team="South Korea",
            commence_time=FIX_KICKOFF,
            competition="FIFA World Cup",
            competition_id=FIX_COMPETITION_ID,
            match_url=FIX_MATCH_URL,
            game_detail_fetcher=detail_fetcher,
            persist=False,
        )
        assert out["status"] == resolver.STATUS_RESOLVED
        assert out["confidence"] == resolver.CONFIDENCE_HIGH
        assert out["game_id"] == FIX_GAME_ID
        assert out["home_team_id"] == FIX_HOME_TEAM_ID
        assert out["away_team_id"] == FIX_AWAY_TEAM_ID
        assert out["competition_id"] == FIX_COMPETITION_ID
        assert out["source_url"] == FIX_MATCH_URL
        assert out["resolved_from"] == "url"
        assert out["reason_code"] == resolver.RC_FROM_URL
        assert out["mapping_reason"] == resolver.RC_TEAM_MAPPING_OK

    async def test_url_path_swapped_competitors_remaps_team_ids(self):
        # 365Scores returns competitors in [away, home] order — the
        # resolver MUST detect the swap and reassign team_ids correctly.
        async def detail_fetcher(_game_id: str):
            return _game_doc(swap_competitors_order=True)

        out = await resolver.resolve_match_identity(
            internal_match_id=FIX_INTERNAL_ID,
            home_team="Mexico", away_team="South Korea",
            commence_time=FIX_KICKOFF,
            competition_id=FIX_COMPETITION_ID,
            match_url=FIX_MATCH_URL,
            game_detail_fetcher=detail_fetcher,
            persist=False,
        )
        assert out["status"] == resolver.STATUS_RESOLVED
        assert out["home_team_id"] == FIX_HOME_TEAM_ID
        assert out["away_team_id"] == FIX_AWAY_TEAM_ID
        # Source still records what 365Scores literally said.
        assert out["home_team_name_source"] == FIX_HOME_NAME_SRC
        assert out["away_team_name_source"] == FIX_AWAY_NAME_SRC
        assert out["mapping_reason"] == resolver.RC_TEAM_MAPPING_SWAPPED

    async def test_url_path_invalid_team_mapping(self):
        async def detail_fetcher(_game_id: str):
            return _game_doc(home_name="Brazil", away_name="Argentina")

        out = await resolver.resolve_match_identity(
            internal_match_id=FIX_INTERNAL_ID,
            home_team="Mexico", away_team="South Korea",
            commence_time=FIX_KICKOFF,
            match_url=FIX_MATCH_URL,
            game_detail_fetcher=detail_fetcher,
            persist=False,
        )
        assert out["status"] == resolver.STATUS_INVALID_TEAM_MAPPING
        assert out["confidence"] == resolver.CONFIDENCE_LOW
        # Crucial: game_id is surfaced even though the mapping is invalid
        # so the operator can investigate.
        assert out["game_id"] == FIX_GAME_ID

    async def test_url_path_uses_aliases(self):
        # Engine has "México" as home (with accent) and "Corea del Sur"
        # as away (Spanish), 365Scores returns the English names.
        async def detail_fetcher(_game_id: str):
            return _game_doc(home_name="Mexico", away_name="South Korea")

        out = await resolver.resolve_match_identity(
            internal_match_id=FIX_INTERNAL_ID,
            home_team="México", away_team="Corea del Sur",
            commence_time=FIX_KICKOFF,
            match_url=FIX_MATCH_URL,
            game_detail_fetcher=detail_fetcher,
            persist=False,
        )
        assert out["status"] == resolver.STATUS_RESOLVED
        assert out["mapping_reason"] == resolver.RC_TEAM_MAPPING_OK

    async def test_url_path_falls_through_when_detail_fetcher_raises(self):
        # The URL path is best-effort: if the detail fetcher fails AND
        # no games_fetcher is provided, we end up SOURCE_UNAVAILABLE.
        async def detail_fetcher(_game_id: str):
            raise RuntimeError("boom")

        out = await resolver.resolve_match_identity(
            internal_match_id=FIX_INTERNAL_ID,
            home_team="Mexico", away_team="South Korea",
            commence_time=FIX_KICKOFF,
            match_url=FIX_MATCH_URL,
            game_detail_fetcher=detail_fetcher,
            persist=False,
        )
        assert out["status"] == resolver.STATUS_SOURCE_UNAVAILABLE

    async def test_url_path_degrades_to_search_when_detail_is_empty(self):
        # Real-world: 365Scores changed the slug format and the regex
        # caught the competition_id (5930) instead of the game_id. The
        # detail_fetcher then returns an empty payload. The resolver
        # MUST NOT flag INVALID_TEAM_MAPPING — instead it should fall
        # through to the search-by-context path which will find the
        # real game_id via the day listing.
        detail_calls: list[str] = []
        search_days: list[str] = []

        async def detail_fetcher(game_id: str):
            detail_calls.append(game_id)
            # Empty payload — simulates "wrong number caught by regex".
            return {}

        async def games_fetcher(date_iso: str):
            search_days.append(date_iso)
            return [_game_doc()]

        bad_url = (
            "https://www.365scores.com/football/match/"
            "fifa-world-cup-5930/mexico-south-korea-2383-5106-5930"
            # ends in `-{away_id}-{home_id}-{competition_id}` — the
            # regex catches 5930 (competition) as the game_id.
        )
        out = await resolver.resolve_match_identity(
            internal_match_id=FIX_INTERNAL_ID,
            home_team="Mexico", away_team="South Korea",
            commence_time=FIX_KICKOFF,
            competition_id=FIX_COMPETITION_ID,
            match_url=bad_url,
            game_detail_fetcher=detail_fetcher,
            games_fetcher=games_fetcher,
            persist=False,
        )
        assert out["status"] == resolver.STATUS_RESOLVED
        assert out["game_id"] == FIX_GAME_ID
        assert out["resolved_from"] == "search"
        assert len(detail_calls) == 1
        assert len(search_days) == 3   # ±1 day window


# ════════════════════════════════════════════════════════════════════════
# Search-by-context path
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
class TestSearchPath:
    async def test_search_resolves_when_names_and_kickoff_match(self):
        seen_days: list[str] = []

        async def games_fetcher(date_iso: str):
            seen_days.append(date_iso)
            return [
                _game_doc(),
                # Decoy with totally different teams.
                _game_doc(game_id=9999, home_id=1, away_id=2,
                           home_name="Brazil", away_name="Argentina"),
            ]

        out = await resolver.resolve_match_identity(
            internal_match_id=FIX_INTERNAL_ID,
            home_team="Mexico", away_team="South Korea",
            commence_time=FIX_KICKOFF,
            competition_id=FIX_COMPETITION_ID,
            games_fetcher=games_fetcher,
            persist=False,
        )
        assert out["status"] == resolver.STATUS_RESOLVED
        assert out["game_id"] == FIX_GAME_ID
        assert out["resolved_from"] == "search"
        assert out["reason_code"] == resolver.RC_FROM_SEARCH
        # ±1 day queries.
        assert len(seen_days) == 3

    async def test_search_rejects_wrong_competition_id(self):
        async def games_fetcher(_date_iso: str):
            return [_game_doc(competition_id=9999)]  # wrong tournament

        out = await resolver.resolve_match_identity(
            internal_match_id=FIX_INTERNAL_ID,
            home_team="Mexico", away_team="South Korea",
            commence_time=FIX_KICKOFF,
            competition_id=FIX_COMPETITION_ID,
            games_fetcher=games_fetcher,
            persist=False,
        )
        assert out["status"] == resolver.STATUS_NOT_FOUND
        assert out["reason_code"] == resolver.RC_NO_CANDIDATES

    async def test_search_respects_6h_tolerance(self):
        # Game kicks off 8 hours after the engine's commence_time —
        # outside the default ±6h window.
        far_kickoff = FIX_KICKOFF + timedelta(hours=8)

        async def games_fetcher(_date_iso: str):
            return [_game_doc(kickoff=far_kickoff)]

        out = await resolver.resolve_match_identity(
            internal_match_id=FIX_INTERNAL_ID,
            home_team="Mexico", away_team="South Korea",
            commence_time=FIX_KICKOFF,
            tolerance_hours=6.0,
            competition_id=FIX_COMPETITION_ID,
            games_fetcher=games_fetcher,
            persist=False,
        )
        assert out["status"] == resolver.STATUS_NOT_FOUND

        # Widen the window → resolves.
        out2 = await resolver.resolve_match_identity(
            internal_match_id=FIX_INTERNAL_ID,
            home_team="Mexico", away_team="South Korea",
            commence_time=FIX_KICKOFF,
            tolerance_hours=12.0,
            competition_id=FIX_COMPETITION_ID,
            games_fetcher=games_fetcher,
            persist=False,
        )
        assert out2["status"] == resolver.STATUS_RESOLVED

    async def test_search_marks_ambiguous_when_two_close_candidates(self):
        # Two games, same teams, both inside ±6h. We expect AMBIGUOUS.
        async def games_fetcher(_date_iso: str):
            return [
                _game_doc(),
                _game_doc(
                    game_id=FIX_GAME_ID + 1,
                    kickoff=FIX_KICKOFF + timedelta(minutes=10),
                ),
            ]

        out = await resolver.resolve_match_identity(
            internal_match_id=FIX_INTERNAL_ID,
            home_team="Mexico", away_team="South Korea",
            commence_time=FIX_KICKOFF,
            competition_id=FIX_COMPETITION_ID,
            games_fetcher=games_fetcher,
            persist=False,
        )
        assert out["status"] == resolver.STATUS_AMBIGUOUS
        assert out["reason_code"] == resolver.RC_MULTIPLE_CANDIDATES
        assert len(out["candidates"]) == 2

    async def test_search_marks_source_unavailable_when_fetcher_raises(self):
        async def games_fetcher(_date_iso: str):
            raise TimeoutError("scrape.do timeout")

        out = await resolver.resolve_match_identity(
            internal_match_id=FIX_INTERNAL_ID,
            home_team="Mexico", away_team="South Korea",
            commence_time=FIX_KICKOFF,
            competition_id=FIX_COMPETITION_ID,
            games_fetcher=games_fetcher,
            persist=False,
        )
        assert out["status"] == resolver.STATUS_SOURCE_UNAVAILABLE
        assert out["reason_code"] == resolver.RC_SOURCE_TIMEOUT

    async def test_search_invalid_team_mapping_on_decoy_swap(self):
        # Same teams swapped but the alias map should detect this as
        # SWAPPED (valid). To force INVALID_TEAM_MAPPING in the search
        # path we use teams that pass the prefilter (name overlap) but
        # the canonical name is different. The prefilter only accepts
        # candidates whose names match; so an INVALID_TEAM_MAPPING
        # outcome in practice is unreachable from the search path
        # (it's reserved for the URL path where the slug forces a
        # specific game_id). This test documents that contract.
        async def games_fetcher(_date_iso: str):
            return [_game_doc(home_name="Brazil", away_name="Argentina")]

        out = await resolver.resolve_match_identity(
            internal_match_id=FIX_INTERNAL_ID,
            home_team="Mexico", away_team="South Korea",
            commence_time=FIX_KICKOFF,
            competition_id=FIX_COMPETITION_ID,
            games_fetcher=games_fetcher,
            persist=False,
        )
        assert out["status"] == resolver.STATUS_NOT_FOUND


# ════════════════════════════════════════════════════════════════════════
# Mongo cache + persistence
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
class TestMongoIntegration:
    async def test_resolved_identity_is_persisted(self):
        db = FakeDB()

        async def games_fetcher(_date_iso: str):
            return [_game_doc()]

        out = await resolver.resolve_match_identity(
            internal_match_id=FIX_INTERNAL_ID,
            home_team="Mexico", away_team="South Korea",
            commence_time=FIX_KICKOFF,
            competition_id=FIX_COMPETITION_ID,
            games_fetcher=games_fetcher,
            db=db, persist=True,
        )
        assert out["status"] == resolver.STATUS_RESOLVED
        coll = db.football_365scores_identities
        assert len(coll.docs) == 1
        stored = coll.docs[0]
        assert stored["internal_match_id"] == FIX_INTERNAL_ID
        assert stored["game_id"] == FIX_GAME_ID
        assert stored["home_team_id"] == FIX_HOME_TEAM_ID
        assert stored["away_team_id"] == FIX_AWAY_TEAM_ID
        assert stored["last_verified_at"]

    async def test_cache_hit_skips_search(self):
        db = FakeDB()
        # Pre-seed cache.
        await db.football_365scores_identities.insert_one({
            "internal_match_id":  FIX_INTERNAL_ID,
            "game_id":            FIX_GAME_ID,
            "competition_id":     FIX_COMPETITION_ID,
            "home_team_id":       FIX_HOME_TEAM_ID,
            "away_team_id":       FIX_AWAY_TEAM_ID,
            "status":             resolver.STATUS_RESOLVED,
            "confidence":         resolver.CONFIDENCE_HIGH,
            "source":             resolver.SOURCE_LABEL,
            "resolved_from":      "search",
        })
        fetcher_calls: list[str] = []

        async def games_fetcher(date_iso: str):
            fetcher_calls.append(date_iso)
            return []

        out = await resolver.resolve_match_identity(
            internal_match_id=FIX_INTERNAL_ID,
            home_team="Mexico", away_team="South Korea",
            commence_time=FIX_KICKOFF,
            competition_id=FIX_COMPETITION_ID,
            games_fetcher=games_fetcher,
            db=db, persist=True,
        )
        assert out["status"] == resolver.STATUS_RESOLVED
        assert out["reason_code"] == resolver.RC_FROM_MONGO_CACHE
        assert out["resolved_from"] == "mongo_cache"
        assert fetcher_calls == []  # cache hit means no fetch happened

    async def test_force_refresh_bypasses_cache(self):
        db = FakeDB()
        await db.football_365scores_identities.insert_one({
            "internal_match_id":  FIX_INTERNAL_ID,
            "game_id":            999999,  # stale!
            "status":             resolver.STATUS_RESOLVED,
            "confidence":         resolver.CONFIDENCE_HIGH,
        })
        fetcher_calls: list[str] = []

        async def games_fetcher(date_iso: str):
            fetcher_calls.append(date_iso)
            return [_game_doc()]

        out = await resolver.resolve_match_identity(
            internal_match_id=FIX_INTERNAL_ID,
            home_team="Mexico", away_team="South Korea",
            commence_time=FIX_KICKOFF,
            competition_id=FIX_COMPETITION_ID,
            games_fetcher=games_fetcher,
            db=db, persist=True, force_refresh=True,
        )
        assert out["status"] == resolver.STATUS_RESOLVED
        assert out["game_id"] == FIX_GAME_ID
        assert len(fetcher_calls) > 0
        # And the cache has been refreshed.
        coll = db.football_365scores_identities
        stored = next(d for d in coll.docs
                      if d["internal_match_id"] == FIX_INTERNAL_ID)
        assert stored["game_id"] == FIX_GAME_ID

    async def test_ensure_indexes_creates_three_indexes(self):
        db = FakeDB()
        report = await resolver.ensure_indexes(db)
        assert "ix_internal_match_id" in report["created"]
        assert "ix_game_id" in report["created"]
        assert "ix_teams_commence" in report["created"]
        # Verify uniqueness flags.
        idx = db.football_365scores_identities.indexes_created
        names_to_kwargs = {kw.get("name"): kw for _, kw in idx}
        assert names_to_kwargs["ix_internal_match_id"]["unique"] is True
        assert names_to_kwargs["ix_game_id"]["unique"] is True

    async def test_ensure_indexes_with_no_db_is_noop(self):
        report = await resolver.ensure_indexes(None)
        assert report["created"] == []
        assert report["skipped"] == "no_db"


# ════════════════════════════════════════════════════════════════════════
# Side flag (isHome) handling
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
class TestIsHomeFlag:
    async def test_is_home_flag_overrides_positional_order(self):
        async def detail_fetcher(_game_id: str):
            # Positions inverted but isHome flag is correct.
            return _game_doc(
                swap_competitors_order=True, use_is_home_flag=True,
            )

        out = await resolver.resolve_match_identity(
            internal_match_id=FIX_INTERNAL_ID,
            home_team="Mexico", away_team="South Korea",
            commence_time=FIX_KICKOFF,
            match_url=FIX_MATCH_URL,
            game_detail_fetcher=detail_fetcher,
            persist=False,
        )
        assert out["status"] == resolver.STATUS_RESOLVED
        # isHome flag wins → mapping is ALIGNED, IDs map correctly.
        assert out["home_team_id"] == FIX_HOME_TEAM_ID
        assert out["away_team_id"] == FIX_AWAY_TEAM_ID
        assert out["mapping_reason"] == resolver.RC_TEAM_MAPPING_OK


# ════════════════════════════════════════════════════════════════════════
# Real 365Scores listing format (homeCompetitor / awayCompetitor)
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
class TestHomeAwayCompetitorShape:
    """The ``/web/games/allscores`` endpoint uses explicit
    ``homeCompetitor`` / ``awayCompetitor`` dicts instead of the
    ``competitors`` array. The resolver MUST honour that shape."""

    def _real_listing_shape(self, *, swapped: bool = False) -> dict:
        home = {"id": FIX_HOME_TEAM_ID, "name": FIX_HOME_NAME_SRC,
                "symbolicName": "MEX"}
        away = {"id": FIX_AWAY_TEAM_ID, "name": FIX_AWAY_NAME_SRC,
                "symbolicName": "KOR"}
        return {
            "id":             FIX_GAME_ID,
            "competitionId":  FIX_COMPETITION_ID,
            "startTime":      FIX_KICKOFF.isoformat(),
            "homeCompetitor": away if swapped else home,
            "awayCompetitor": home if swapped else away,
            "competitors":    [],   # listing leaves it empty
        }

    async def test_resolves_from_listing_shape(self):
        listing_game = self._real_listing_shape()

        async def games_fetcher(_date_iso: str):
            return [listing_game]

        out = await resolver.resolve_match_identity(
            internal_match_id=FIX_INTERNAL_ID,
            home_team="Mexico", away_team="South Korea",
            commence_time=FIX_KICKOFF,
            competition_id=FIX_COMPETITION_ID,
            games_fetcher=games_fetcher,
            persist=False,
        )
        assert out["status"] == resolver.STATUS_RESOLVED
        assert out["game_id"] == FIX_GAME_ID
        assert out["home_team_id"] == FIX_HOME_TEAM_ID
        assert out["away_team_id"] == FIX_AWAY_TEAM_ID
        assert out["mapping_reason"] == resolver.RC_TEAM_MAPPING_OK

    async def test_listing_shape_swapped_is_detected(self):
        listing_game = self._real_listing_shape(swapped=True)

        async def games_fetcher(_date_iso: str):
            return [listing_game]

        out = await resolver.resolve_match_identity(
            internal_match_id=FIX_INTERNAL_ID,
            home_team="Mexico", away_team="South Korea",
            commence_time=FIX_KICKOFF,
            competition_id=FIX_COMPETITION_ID,
            games_fetcher=games_fetcher,
            persist=False,
        )
        assert out["status"] == resolver.STATUS_RESOLVED
        # IDs realigned to canonical even though source had them swapped.
        assert out["home_team_id"] == FIX_HOME_TEAM_ID
        assert out["away_team_id"] == FIX_AWAY_TEAM_ID
        assert out["mapping_reason"] == resolver.RC_TEAM_MAPPING_SWAPPED
