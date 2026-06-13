"""Phase F85 Phase 2 — Tests for the FBref search-page resolver + fuzzy
matching."""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from services.external_sources import fbref_client as fb


# ─────────────────────────────────────────────────────────────────────
# Search HTML helpers
# ─────────────────────────────────────────────────────────────────────
def _search_results_html(hits: list[dict]) -> str:
    """Build a minimal FBref search-results page from a list of
    ``{name, href, type}`` dicts. ``type`` is one of ``club``,
    ``national-team`` or ``""`` for unknown."""
    items = ""
    for h in hits:
        cls = "search-item"
        if h.get("type"):
            cls += f" search-item-{h['type']}"
        items += (
            f'<div class="{cls}">'
            f'  <div class="search-item-name">'
            f'    <a href="{h["href"]}">{h["name"]}</a>'
            f'  </div>'
            f'</div>'
        )
    return f'<html><body>{items}</body></html>'


# =====================================================================
# _fuzzy_similarity
# =====================================================================
class TestFuzzySimilarity:
    def test_identical_normalised_names_return_one(self):
        # "Paraguay Men" normalises to "paraguay" → match against
        # plain "Paraguay" must score 1.0.
        assert fb._fuzzy_similarity("Paraguay", "Paraguay Men") == 1.0

    def test_accents_are_collapsed(self):
        # "México" vs "Mexico" → identical after normalisation.
        assert fb._fuzzy_similarity("México", "Mexico") == 1.0

    def test_dissimilar_names_score_low(self):
        score = fb._fuzzy_similarity("Argentina", "Paraguay")
        assert score < 0.6

    @pytest.mark.parametrize("a,b", [("", "x"), ("x", ""), (None, None)])
    def test_empty_inputs_return_zero(self, a, b):
        assert fb._fuzzy_similarity(a, b) == 0.0


# =====================================================================
# _parse_fbref_search_results
# =====================================================================
class TestParseSearchResults:
    def test_extracts_clubs_and_national_teams(self):
        html = _search_results_html([
            {"name": "Manchester United", "type": "club",
             "href": "/en/squads/19538871/Manchester-United-Stats"},
            {"name": "England Men", "type": "national-team",
             "href": "/en/squads/0eb73e51/England-Men-Stats"},
        ])
        out = fb._parse_fbref_search_results(html)
        assert len(out) == 2
        # Type detection via container class.
        types = {c["display"]: c["team_type"] for c in out}
        assert types["Manchester United"] == "club"
        assert types["England Men"]      == "national_team"
        # URLs become absolute.
        urls = [c["url"] for c in out]
        assert all(u.startswith("https://fbref.com/en/squads/") for u in urls)
        # Normalised name strips "Men" suffix.
        norms = {c["display"]: c["name"] for c in out}
        assert norms["England Men"] == "england"

    def test_skips_non_squad_links(self):
        """Players / coaches / competitions live under other prefixes —
        the parser must NOT return them."""
        html = (
            '<div class="search-item search-item-player">'
            '  <div class="search-item-name">'
            '    <a href="/en/players/abc/Lionel-Messi">Lionel Messi</a>'
            '  </div></div>'
            '<div class="search-item search-item-club">'
            '  <div class="search-item-name">'
            '    <a href="/en/squads/xyz/Boca">Boca Juniors</a>'
            '  </div></div>'
        )
        out = fb._parse_fbref_search_results(html)
        assert len(out) == 1
        assert out[0]["display"] == "Boca Juniors"
        assert out[0]["team_type"] == "club"

    @pytest.mark.parametrize("bad", [None, "", "<not-html"])
    def test_invalid_inputs_return_empty(self, bad):
        assert fb._parse_fbref_search_results(bad) == []


# =====================================================================
# _best_fuzzy_hit
# =====================================================================
class TestBestFuzzyHit:
    def test_returns_above_threshold_candidate(self):
        cands = [
            {"display": "Paraguay Men", "url": "U1", "team_type": "national_team"},
            {"display": "Uruguay Men",  "url": "U2", "team_type": "national_team"},
        ]
        hit = fb._best_fuzzy_hit(cands, "Paraguay")
        assert hit is not None
        assert hit["display"] == "Paraguay Men"
        assert hit["fuzzy_score"] >= fb.SEARCH_FUZZY_THRESHOLD
        # Original candidate object MUST NOT be mutated.
        assert "fuzzy_score" not in cands[0]

    def test_returns_none_when_below_threshold(self):
        cands = [{"display": "Manchester City", "url": "X", "team_type": "club"}]
        # Query has a completely different shape → fuzzy score < threshold.
        assert fb._best_fuzzy_hit(cands, "Tokyo Verdy") is None

    def test_picks_best_among_multiple_above_threshold(self):
        cands = [
            {"display": "United States Women", "url": "W"},  # below threshold (accent missing)
            {"display": "United States Men",   "url": "M"},  # 1.0
            {"display": "United States U20",   "url": "U20"},
        ]
        hit = fb._best_fuzzy_hit(cands, "United States")
        assert hit["url"] == "M"

    def test_empty_candidates_returns_none(self):
        assert fb._best_fuzzy_hit([], "Anything") is None


# =====================================================================
# _search_fbref_for_team (network layer)
# =====================================================================
class TestSearchFbrefForTeam:
    @pytest.mark.asyncio
    async def test_returns_parsed_candidates_from_results_page(self):
        html = _search_results_html([
            {"name": "Paraguay Men", "type": "national-team",
             "href": "/en/squads/b8f1bbb1/Paraguay-Men-Stats"},
        ])

        def handler(req: httpx.Request) -> httpx.Response:
            assert "/en/search/search.fcgi" in req.url.path
            assert "search=Paraguay" in str(req.url)
            return httpx.Response(200, text=html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            out = await fb._search_fbref_for_team(c, "Paraguay")
        assert len(out) == 1
        assert out[0]["url"].endswith("/Paraguay-Men-Stats")

    @pytest.mark.asyncio
    async def test_single_hit_redirect_synthesises_candidate(self):
        """When FBref redirects to /en/squads/... directly, we should
        treat that as one candidate matching the query."""
        def handler(req):
            return httpx.Response(
                302,
                headers={"location": "/en/squads/abc/Solo-Hit-Stats"},
                text="",
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            out = await fb._search_fbref_for_team(c, "Solo Hit")
        assert len(out) == 1
        assert out[0]["url"].endswith("/Solo-Hit-Stats")
        assert out[0]["display"] == "Solo Hit"

    @pytest.mark.asyncio
    async def test_redirect_to_non_squads_returns_empty(self):
        def handler(req):
            return httpx.Response(
                302, headers={"location": "/en/comps/9/Premier-League-Stats"},
                text="",
            )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            out = await fb._search_fbref_for_team(c, "Premier League")
        assert out == []

    @pytest.mark.asyncio
    async def test_404_returns_empty(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(404, text=""),
        )) as c:
            out = await fb._search_fbref_for_team(c, "Whatever FC")
        assert out == []

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, text=""),
        )) as c:
            assert await fb._search_fbref_for_team(c, "") == []


# =====================================================================
# resolve_fbref_team_url integration: Phase 2 tier
# =====================================================================
class TestResolveFbrefTeamUrlPhase2:
    @pytest.mark.asyncio
    async def test_falls_back_to_search_fuzzy_for_unknown_team(self):
        """Static + Mongo both miss → Phase 2 search kicks in and
        returns a fuzzy hit with ``source='search_fuzzy'``."""
        html = _search_results_html([
            {"name": "Honduras Men", "type": "national-team",
             "href": "/en/squads/abc1234/Honduras-Men-Stats"},
        ])

        def handler(req):
            if "/en/search/search.fcgi" in req.url.path:
                return httpx.Response(200, text=html)
            return httpx.Response(404, text="")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            out = await fb.resolve_fbref_team_url(c, "Honduras", db=None)
        assert out["available"] is True
        assert out["source"] == "search_fuzzy"
        assert out["url"].endswith("/Honduras-Men-Stats")
        assert out["fuzzy_score"] >= fb.SEARCH_FUZZY_THRESHOLD
        assert out["team_type"] == "national_team"

    @pytest.mark.asyncio
    async def test_static_mapping_still_wins_over_phase_2(self):
        """USA is in the static table — the resolver MUST short-circuit
        without hitting the network."""
        called = {"n": 0}

        def handler(req):
            called["n"] += 1
            return httpx.Response(404, text="")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            out = await fb.resolve_fbref_team_url(c, "USA", db=None)
        assert out["source"] == "static_mapping"
        # No search request was made.
        assert called["n"] == 0

    @pytest.mark.asyncio
    async def test_phase_2_miss_returns_team_url_missing(self):
        """Search returns a candidate that fails the fuzzy threshold →
        the resolver must still report ``available=False``."""
        html = _search_results_html([
            {"name": "Tokyo Verdy", "type": "club",
             "href": "/en/squads/xyz/Tokyo-Verdy-Stats"},
        ])

        def handler(req):
            return httpx.Response(200, text=html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            out = await fb.resolve_fbref_team_url(c, "Atlantis FC Phantom", db=None)
        assert out["available"] is False
        assert fb.RC_TEAM_URL_MISSING in out["reason_codes"]

    @pytest.mark.asyncio
    async def test_phase_2_skipped_when_client_is_none(self):
        """Unit-test mode (``client=None``) MUST never trigger network
        I/O, even when static + Mongo miss."""
        out = await fb.resolve_fbref_team_url(None, "Honduras", db=None)
        assert out["available"] is False
        assert fb.RC_TEAM_URL_MISSING in out["reason_codes"]


# =====================================================================
# _persist_search_hit_to_mongo
# =====================================================================
class TestPersistSearchHitToMongo:
    @pytest.mark.asyncio
    async def test_upserts_payload_with_provenance_metadata(self):
        captured: dict[str, Any] = {}

        class FakeColl:
            async def update_one(self, q, update, *, upsert):
                captured["query"]  = q
                captured["update"] = update
                captured["upsert"] = upsert
                return type("R", (), {"modified_count": 1, "upserted_id": None})()

        class FakeDB:
            def __getitem__(self, name):
                return FakeColl()

        await fb._persist_search_hit_to_mongo(
            FakeDB(),
            team_name="Honduras",
            team_norm="honduras",
            hit={"url": "https://fbref.com/en/squads/x/y",
                  "display": "Honduras Men",
                  "team_type": "national_team",
                  "fuzzy_score": 0.93},
        )
        q = captured["query"]
        u = captured["update"]
        assert q == {"provider": "fbref", "team_name_norm": "honduras"}
        assert u["$set"]["discovered_via"] == "search_fuzzy"
        assert u["$set"]["fbref_team_url"].endswith("/y")
        assert u["$set"]["fuzzy_score"] == 0.93
        assert captured["upsert"] is True

    @pytest.mark.asyncio
    async def test_persist_is_fail_soft_on_db_error(self):
        class CrashingColl:
            async def update_one(self, *a, **kw):
                raise RuntimeError("mongo down")

        class FakeDB:
            def __getitem__(self, name):
                return CrashingColl()

        # MUST NOT raise.
        await fb._persist_search_hit_to_mongo(
            FakeDB(), team_name="X", team_norm="x",
            hit={"url": "U", "display": "X"},
        )

    @pytest.mark.asyncio
    async def test_persist_skips_when_db_is_none(self):
        # No-op, no exception.
        await fb._persist_search_hit_to_mongo(
            None, team_name="X", team_norm="x", hit={"url": "U"},
        )
