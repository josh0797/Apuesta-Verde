"""F96.1 — Tests del extractor de corners + lookup TheStatsAPI + integración settler.

Cobertura:
  - `_extract_corners_from_payload` para múltiples shapes:
      * flat (home_corners/away_corners)
      * corners.{home,away}
      * corners scalar (total only) → returns (None, None) with raw_name
      * stats list con name normalizado
      * nested team stats
  - `_lookup_corners_from_thestatsapi`:
      * happy path → ({"available": True, total_corners=N})
      * stats payload vacío → not available
      * client disabled → reason code
      * fetch raises → fail-soft
  - `lookup_total_corners` cascade orchestrator:
      * TheStatsAPI succeeds → short-circuit
      * TheStatsAPI fails → TheSportsDB stub returns "no data"
        (will be replaced in F96.2)
      * always returns canonical shape
  - Integración settler:
      * Cuando corners están disponibles, `outputs["total_corners"]` se
        propaga al `settle_post_match`.
      * Cuando NO están, `outputs` no incluye `total_corners`.
      * `summary["corners"]` se actualiza correctamente.
  - Alias normalisation:
      * acepta "Corners", "CORNER KICKS", "corner_kicks", " Total Corners ".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services import football_finished_game_settler as fgs


# ────────────────────────────────────────────────────────────────────────
# In-memory DB shim (re-used from F95 tests, kept self-contained)
# ────────────────────────────────────────────────────────────────────────
class _AsyncCursor:
    def __init__(self, docs, limit=None):
        self._docs = list(docs)
        self._limit = limit

    def limit(self, n):
        self._limit = n
        return self

    def __aiter__(self):
        self._iter = iter(self._docs[: self._limit] if self._limit else self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _AsyncCollection:
    def __init__(self, docs=None):
        self.docs = docs or []

    async def find_one(self, query):
        match_id = query.get("match_id") if isinstance(query, dict) else None
        for d in self.docs:
            if d.get("match_id") == match_id:
                return d
        return None

    def find(self, query=None):
        return _AsyncCursor(self.docs)


class _AsyncDB:
    def __init__(self):
        self.collections: dict[str, _AsyncCollection] = {}

    def __getitem__(self, name):
        if name not in self.collections:
            self.collections[name] = _AsyncCollection()
        return self.collections[name]


def _snap(*, match_id="m-1", hours_ago=5):
    now = datetime.now(timezone.utc)
    kickoff = now - timedelta(hours=hours_ago)
    return {
        "match_id":          match_id,
        "sport":             "football",
        "home_team":         {"name": "Brazil"},
        "away_team":         {"name": "Haiti"},
        "match_date":        kickoff,
        "snapshot_taken_at": kickoff - timedelta(hours=4),
        "reason_codes":      ["PRE_MATCH_SNAPSHOT_CREATED"],
    }


# =====================================================================
# _extract_corners_from_payload
# =====================================================================
class TestExtractCornersShapes:
    def test_flat_keys(self):
        payload = {"home_corners": 6, "away_corners": 4}
        h, a, names = fgs._extract_corners_from_payload(payload)
        assert (h, a) == (6, 4)
        assert "home_corners" in names

    def test_corners_dict_home_away(self):
        payload = {"corners": {"home": 7, "away": 3}}
        h, a, _ = fgs._extract_corners_from_payload(payload)
        assert (h, a) == (7, 3)

    def test_corners_dict_total_only_is_partial(self):
        payload = {"corners": {"total": 9}}
        h, a, names = fgs._extract_corners_from_payload(payload)
        assert (h, a) == (None, None)
        assert "corners" in names

    def test_corners_scalar_total_is_partial(self):
        payload = {"corners": 11}
        h, a, names = fgs._extract_corners_from_payload(payload)
        assert (h, a) == (None, None)
        assert "corners" in names

    def test_stats_list_with_name_home_away(self):
        payload = {
            "stats": [
                {"name": "Possession", "home": 55, "away": 45},
                {"name": "Corners", "home": 8, "away": 2},
                {"name": "Shots", "home": 12, "away": 10},
            ],
        }
        h, a, names = fgs._extract_corners_from_payload(payload)
        assert (h, a) == (8, 2)
        # Raw names captured for debug.
        assert "Corners" in names

    def test_stats_list_with_corner_kicks_alias(self):
        payload = {
            "stats": [
                {"name": "Corner Kicks", "home": 5, "away": 5},
            ],
        }
        h, a, _ = fgs._extract_corners_from_payload(payload)
        assert (h, a) == (5, 5)

    def test_stats_list_with_underscore_alias(self):
        payload = {
            "stats": [
                {"name": "corner_kicks", "home": 3, "away": 7},
            ],
        }
        h, a, _ = fgs._extract_corners_from_payload(payload)
        assert (h, a) == (3, 7)

    def test_stats_list_with_total_only_is_partial(self):
        payload = {
            "stats": [
                {"name": "Total Corners", "value": 10},
            ],
        }
        h, a, names = fgs._extract_corners_from_payload(payload)
        assert (h, a) == (None, None)
        assert "Total Corners" in names

    def test_stats_list_with_parenthesised_qualifier(self):
        payload = {
            "stats": [
                {"name": "Corners (Total)", "home": 4, "away": 6},
            ],
        }
        h, a, _ = fgs._extract_corners_from_payload(payload)
        assert (h, a) == (4, 6)

    def test_nested_team_stats(self):
        payload = {
            "home_team": {"stats": {"Possession": 55, "Corners": 9}},
            "away_team": {"stats": {"Possession": 45, "Corners": 3}},
        }
        h, a, names = fgs._extract_corners_from_payload(payload)
        assert (h, a) == (9, 3)
        # We should have observed "Corners" from at least one side.
        assert any(_normal(n) == "corners" for n in names)

    def test_no_corner_data_returns_none_none(self):
        payload = {
            "stats": [
                {"name": "Possession", "home": 55, "away": 45},
                {"name": "Shots", "home": 12, "away": 10},
            ],
        }
        h, a, names = fgs._extract_corners_from_payload(payload)
        assert (h, a) == (None, None)
        # raw_names should still capture observed non-corner names.
        assert "Possession" in names
        assert "Shots" in names

    def test_non_dict_input_returns_none_none_empty(self):
        for bad in [None, [], "string", 42]:
            h, a, names = fgs._extract_corners_from_payload(bad)
            assert (h, a) == (None, None)
            assert names == []

    def test_alias_case_insensitive_and_padded(self):
        payload = {
            "stats": [
                {"name": "  CORNER KICKS  ", "home": 4, "away": 5},
            ],
        }
        h, a, _ = fgs._extract_corners_from_payload(payload)
        assert (h, a) == (4, 5)


# =====================================================================
# _name_matches_corner
# =====================================================================
class TestNameMatchesCorner:
    @pytest.mark.parametrize("name", [
        "Corners", "CORNERS", "corner_kicks", " Corner Kicks ",
        "Total Corners", "corners total", "Corners (Total)",
        "TOTAL_CORNERS", "totalCorners",
    ])
    def test_aliases_recognised(self, name):
        assert fgs._name_matches_corner(name) is True

    @pytest.mark.parametrize("name", [
        "Possession", "Shots", "Offsides", "Cards", "Tackles",
        "", None, 42, "corner flag",
    ])
    def test_non_corner_names_rejected(self, name):
        assert fgs._name_matches_corner(name) is False


# =====================================================================
# _lookup_corners_from_thestatsapi
# =====================================================================
class TestLookupCornersTheStatsAPI:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        with patch("services.external_sources.thestatsapi_client.is_enabled",
                   return_value=True), \
             patch("services.external_sources.thestatsapi_client.fetch_match_stats",
                   new=AsyncMock(return_value={"home_corners": 7,
                                                "away_corners": 3})):
            res = await fgs._lookup_corners_from_thestatsapi(
                "m-1", http_client=None,
            )
        assert res["available"] is True
        assert res["home_corners"] == 7
        assert res["away_corners"] == 3
        assert res["total_corners"] == 10
        assert res["source"] == fgs.PROVIDER_THESTATSAPI
        assert fgs.RC_CORNERS_FROM_THESTATSAPI in res["reason_codes"]

    @pytest.mark.asyncio
    async def test_empty_payload_not_available(self):
        with patch("services.external_sources.thestatsapi_client.is_enabled",
                   return_value=True), \
             patch("services.external_sources.thestatsapi_client.fetch_match_stats",
                   new=AsyncMock(return_value={})):
            res = await fgs._lookup_corners_from_thestatsapi(
                "m-1", http_client=None,
            )
        assert res["available"] is False
        assert res["total_corners"] is None

    @pytest.mark.asyncio
    async def test_disabled_client(self):
        with patch("services.external_sources.thestatsapi_client.is_enabled",
                   return_value=False):
            res = await fgs._lookup_corners_from_thestatsapi(
                "m-1", http_client=None,
            )
        assert res["available"] is False
        assert "THESTATSAPI_DISABLED" in res["reason_codes"]

    @pytest.mark.asyncio
    async def test_fetch_raises_fail_soft(self):
        with patch("services.external_sources.thestatsapi_client.is_enabled",
                   return_value=True), \
             patch("services.external_sources.thestatsapi_client.fetch_match_stats",
                   new=AsyncMock(side_effect=RuntimeError("network down"))):
            res = await fgs._lookup_corners_from_thestatsapi(
                "m-1", http_client=None,
            )
        assert res["available"] is False
        assert "THESTATSAPI_FETCH_FAILED" in res["reason_codes"]

    @pytest.mark.asyncio
    async def test_partial_corners_total_only_not_available(self):
        with patch("services.external_sources.thestatsapi_client.is_enabled",
                   return_value=True), \
             patch("services.external_sources.thestatsapi_client.fetch_match_stats",
                   new=AsyncMock(return_value={"corners": {"total": 11}})):
            res = await fgs._lookup_corners_from_thestatsapi(
                "m-1", http_client=None,
            )
        assert res["available"] is False
        assert res["total_corners"] is None
        # Debug info captured.
        assert "corners" in res["raw_names"]


# =====================================================================
# lookup_total_corners cascade
# =====================================================================
class TestLookupTotalCornersCascade:
    @pytest.mark.asyncio
    async def test_thestatsapi_short_circuits(self):
        async def ok_ts(match_id, *, http_client):
            return {
                "available":     True,
                "home_corners":  4,
                "away_corners":  6,
                "total_corners": 10,
                "source":        fgs.PROVIDER_THESTATSAPI,
                "raw_names":     ["Corners"],
                "reason_codes":  [fgs.RC_CORNERS_FROM_THESTATSAPI],
            }

        with patch.object(fgs, "_lookup_corners_from_thestatsapi",
                          side_effect=ok_ts), \
             patch.object(fgs, "_lookup_corners_from_thesportsdb",
                          new=AsyncMock()) as m_sdb:
            res = await fgs.lookup_total_corners(
                "m-1", _snap(), http_client=None,
            )
        assert res["available"] is True
        assert res["total_corners"] == 10
        assert res["source"] == fgs.PROVIDER_THESTATSAPI
        m_sdb.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_falls_through_to_thesportsdb_stub(self):
        async def empty_ts(match_id, *, http_client):
            return {"available": False, "home_corners": None,
                    "away_corners": None, "total_corners": None,
                    "source": None, "raw_names": [], "reason_codes": []}

        with patch.object(fgs, "_lookup_corners_from_thestatsapi",
                          side_effect=empty_ts):
            res = await fgs.lookup_total_corners(
                "m-1", _snap(), http_client=None,
            )
        # F96.2 stub returns not available; cascade exposes the final RC.
        assert res["available"] is False
        assert res["total_corners"] is None
        assert fgs.RC_CORNERS_NOT_AVAILABLE in res["reason_codes"]

    @pytest.mark.asyncio
    async def test_thesportsdb_raises_fail_soft(self):
        async def empty_ts(match_id, *, http_client):
            return {"available": False, "home_corners": None,
                    "away_corners": None, "total_corners": None,
                    "source": None, "raw_names": [], "reason_codes": []}

        async def boom(snap, *, http_client):
            raise RuntimeError("provider exploded")

        with patch.object(fgs, "_lookup_corners_from_thestatsapi",
                          side_effect=empty_ts), \
             patch.object(fgs, "_lookup_corners_from_thesportsdb",
                          side_effect=boom):
            res = await fgs.lookup_total_corners(
                "m-1", _snap(), http_client=None,
            )
        assert res["available"] is False  # never raises
        assert isinstance(res["reason_codes"], list)

    @pytest.mark.asyncio
    async def test_thestatsapi_raises_falls_through(self):
        async def boom_ts(match_id, *, http_client):
            raise RuntimeError("network down")

        with patch.object(fgs, "_lookup_corners_from_thestatsapi",
                          side_effect=boom_ts):
            res = await fgs.lookup_total_corners(
                "m-1", _snap(), http_client=None,
            )
        assert res["available"] is False  # no raise


# =====================================================================
# Settler integración — corners propagados
# =====================================================================
class TestSettlerCornersIntegration:
    @pytest.mark.asyncio
    async def test_total_corners_added_to_outputs_when_available(self):
        db = _AsyncDB()
        db.collections[fgs.COLLECTION_SNAPSHOTS] = _AsyncCollection([
            _snap(match_id="m-1", hours_ago=5),
        ])
        db.collections["matches"] = _AsyncCollection()

        captured: dict = {}

        async def fake_settle(db_, *, match_id, outputs, source_audit_entries=None):
            captured["outputs"] = outputs
            captured["audit"]   = list(source_audit_entries or [])
            return {"reason_codes": ["POST_MATCH_RESULT_SETTLED"]}

        async def fake_score(*args, **kwargs):
            return {
                "available": True, "home_goals": 2, "away_goals": 1,
                "source": fgs.PROVIDER_THESTATSAPI,
                "reason_codes": [fgs.RC_SETTLER_FROM_THESTATSAPI],
            }

        async def fake_corners(*args, **kwargs):
            return {
                "available": True, "home_corners": 5, "away_corners": 4,
                "total_corners": 9,
                "source": fgs.PROVIDER_THESTATSAPI,
                "raw_names": ["Corners"],
                "reason_codes": [fgs.RC_CORNERS_FROM_THESTATSAPI],
            }

        with patch.object(fgs, "lookup_final_score", side_effect=fake_score), \
             patch.object(fgs, "lookup_total_corners", side_effect=fake_corners):
            summary = await fgs.settle_recent_finished_football(
                db, settle_fn=fake_settle,
            )

        assert summary["settled_full"] == 1
        assert summary["corners"]["attempted"] == 1
        assert summary["corners"]["hydrated"] == 1
        assert summary["corners"]["providers"][fgs.PROVIDER_THESTATSAPI] == 1
        assert captured["outputs"]["total_corners"] == 9
        # Two audit entries: score + corners.
        assert len(captured["audit"]) == 2
        corners_entry = captured["audit"][1]
        assert corners_entry["stage"] == "football_finished_game_settler:corners"
        assert corners_entry["home_corners"] == 5
        assert corners_entry["away_corners"] == 4

    @pytest.mark.asyncio
    async def test_corners_missing_does_not_block_score_settle(self):
        db = _AsyncDB()
        db.collections[fgs.COLLECTION_SNAPSHOTS] = _AsyncCollection([
            _snap(match_id="m-1", hours_ago=5),
        ])
        db.collections["matches"] = _AsyncCollection()

        captured: dict = {}

        async def fake_settle(db_, *, match_id, outputs, source_audit_entries=None):
            captured["outputs"] = outputs
            captured["audit"]   = list(source_audit_entries or [])
            return {"reason_codes": ["POST_MATCH_RESULT_SETTLED",
                                      "POST_MATCH_CORNERS_MISSING"]}

        async def fake_score(*args, **kwargs):
            return {
                "available": True, "home_goals": 0, "away_goals": 0,
                "source": fgs.PROVIDER_THESTATSAPI,
                "reason_codes": [fgs.RC_SETTLER_FROM_THESTATSAPI],
            }

        async def fake_corners(*args, **kwargs):
            return {
                "available": False, "home_corners": None,
                "away_corners": None, "total_corners": None,
                "source": None, "raw_names": ["Possession", "Shots"],
                "reason_codes": [fgs.RC_CORNERS_NOT_AVAILABLE],
            }

        with patch.object(fgs, "lookup_final_score", side_effect=fake_score), \
             patch.object(fgs, "lookup_total_corners", side_effect=fake_corners):
            summary = await fgs.settle_recent_finished_football(
                db, settle_fn=fake_settle,
            )

        assert summary["settled_full"] == 1   # final_score still settled
        assert summary["corners"]["hydrated"] == 0
        assert summary["corners"]["not_available"] == 1
        # `total_corners` must NOT be in outputs.
        assert "total_corners" not in captured["outputs"]
        # Audit still records the debug info (raw_names visible).
        corners_entry = next(
            (e for e in captured["audit"]
             if e.get("stage") == "football_finished_game_settler:corners"),
            None,
        )
        assert corners_entry is not None
        assert corners_entry["status"] == "PARTIAL"
        assert "Possession" in corners_entry["raw_names"]

    @pytest.mark.asyncio
    async def test_corners_lookup_raise_does_not_break_settle(self):
        db = _AsyncDB()
        db.collections[fgs.COLLECTION_SNAPSHOTS] = _AsyncCollection([
            _snap(match_id="m-1", hours_ago=5),
        ])
        db.collections["matches"] = _AsyncCollection()

        async def fake_score(*args, **kwargs):
            return {
                "available": True, "home_goals": 1, "away_goals": 1,
                "source": fgs.PROVIDER_THESTATSAPI,
                "reason_codes": [fgs.RC_SETTLER_FROM_THESTATSAPI],
            }

        async def boom_corners(*args, **kwargs):
            raise RuntimeError("corners provider blew up")

        async def fake_settle(db_, **kwargs):
            return {"reason_codes": ["POST_MATCH_RESULT_SETTLED"]}

        with patch.object(fgs, "lookup_final_score", side_effect=fake_score), \
             patch.object(fgs, "lookup_total_corners", side_effect=boom_corners):
            summary = await fgs.settle_recent_finished_football(
                db, settle_fn=fake_settle,
            )

        # Score settle still succeeded; corners failure counted as not-available.
        assert summary["settled_full"] == 1
        assert summary["corners"]["not_available"] == 1


# =====================================================================
# Públicos
# =====================================================================
class TestPublicSymbolsF96:
    def test_corners_constants_exported(self):
        for name in (
            "lookup_total_corners",
            "RC_CORNERS_FROM_THESTATSAPI",
            "RC_CORNERS_FROM_THESPORTSDB",
            "RC_THESPORTSDB_CORNERS_NOT_AVAILABLE",
            "RC_PARTIAL_CORNERS_DATA",
            "RC_CORNERS_NOT_AVAILABLE",
            "CORNER_STAT_ALIASES",
        ):
            assert hasattr(fgs, name), f"missing public symbol: {name}"


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────
def _normal(s: str) -> str:
    return fgs._normalise_stat_name(s)
