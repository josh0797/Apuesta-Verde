"""Phase F85.1 — Tests for the FBref public scraper / parser.

We never hit the real FBref. Everything that touches the network goes
through ``httpx.MockTransport``; HTML payloads are inlined fixtures
that mirror FBref's real DOM structure (including the in-comment table
trick).
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from services.external_sources import fbref_client as fb


# ─────────────────────────────────────────────────────────────────────
# Fixture HTML snippets
# ─────────────────────────────────────────────────────────────────────
def _build_matchlogs_html(rows: list[dict], *, table_id="matchlogs_for") -> str:
    """Build a minimal FBref-style match-logs table from the given rows."""
    header_cells = "".join([
        '<th data-stat="date">Date</th>',
        '<th data-stat="comp">Comp</th>',
        '<th data-stat="venue">Venue</th>',
        '<th data-stat="result">Result</th>',
        '<th data-stat="opponent">Opponent</th>',
        '<th data-stat="xg_for">xG</th>',
        '<th data-stat="xg_against">xGA</th>',
        '<th data-stat="npxg_for">npxG</th>',
        '<th data-stat="npxg_against">npxGA</th>',
        '<th data-stat="shots_for">Sh</th>',
        '<th data-stat="shots_on_target_for">SoT</th>',
        '<th data-stat="possession">Poss</th>',
    ])
    body = ""
    for r in rows:
        body += (
            "<tr>"
            f'<td>{r.get("date","")}</td>'
            f'<td>{r.get("comp","")}</td>'
            f'<td>{r.get("venue","")}</td>'
            f'<td>{r.get("result","")}</td>'
            f'<td>{r.get("opponent","")}</td>'
            f'<td>{r.get("xg","")}</td>'
            f'<td>{r.get("xga","")}</td>'
            f'<td>{r.get("npxg","")}</td>'
            f'<td>{r.get("npxga","")}</td>'
            f'<td>{r.get("sh","")}</td>'
            f'<td>{r.get("sot","")}</td>'
            f'<td>{r.get("poss","")}</td>'
            "</tr>"
        )
    return (
        f'<table id="{table_id}">'
        f'<thead><tr>{header_cells}</tr></thead>'
        f'<tbody>{body}</tbody>'
        '</table>'
    )


def _wrap_in_comment(inner: str) -> str:
    """Mirror FBref's deferred-render trick: hide a table inside an
    HTML comment so it's not in the initial DOM."""
    return f"<div id=\"all\"><!--\n{inner}\n--></div>"


_SAMPLE_ROWS = [
    {"date": "2026-05-10", "comp": "Friendlies", "venue": "Home", "result": "W",
     "opponent": "Costa Rica", "xg": "1.50", "xga": "0.90", "npxg": "1.30",
     "npxga": "0.90", "sh": "12", "sot": "5", "poss": "55"},
    {"date": "2026-05-18", "comp": "Friendlies", "venue": "Away", "result": "L",
     "opponent": "Colombia", "xg": "0.80", "xga": "2.10", "npxg": "0.80",
     "npxga": "2.10", "sh": "8", "sot": "2", "poss": "42"},
    {"date": "2026-06-01", "comp": "Friendlies", "venue": "Home", "result": "D",
     "opponent": "Honduras", "xg": "1.20", "xga": "1.20", "npxg": "1.20",
     "npxga": "1.20", "sh": "10", "sot": "4", "poss": "51"},
    {"date": "2026-06-08", "comp": "Friendlies", "venue": "Neutral", "result": "W",
     "opponent": "Bolivia", "xg": "2.20", "xga": "0.50", "npxg": "1.90",
     "npxga": "0.50", "sh": "16", "sot": "8", "poss": "63"},
    {"date": "2026-06-12", "comp": "Friendlies", "venue": "Home", "result": "W",
     "opponent": "Paraguay", "xg": "1.72", "xga": "0.91", "npxg": "1.45",
     "npxga": "0.91", "sh": "13", "sot": "5", "poss": "58"},
]


# =====================================================================
# Normalisation
# =====================================================================
class TestNormaliseName:
    @pytest.mark.parametrize("raw,expected", [
        ("USA",              "usa"),
        ("United States",    "united states"),
        ("USMNT",            "usmnt"),
        ("Estados Unidos",   "estados unidos"),
        ("México",           "mexico"),
        ("Côte d'Ivoire",    "cote d ivoire"),
        ("Bosnia & Herzegovina", "bosnia and herzegovina"),
        ("England National Team", "england"),
        ("Germany Men",      "germany"),
        ("  Spain  ",        "spain"),
        ("",                 ""),
        (None,               ""),
    ])
    def test_normalisation(self, raw, expected):
        assert fb._normalise_name(raw) == expected


# =====================================================================
# Comment unwrapping
# =====================================================================
class TestUncommentHtml:
    def test_extracts_content_from_html_comment(self):
        html = "<p>before</p><!-- <table id='x'>row</table> --><p>after</p>"
        out = fb._uncomment_html(html)
        assert "<table id='x'>row</table>" in out
        assert "<!--" not in out

    def test_handles_multiline_comments(self):
        html = "<div>top</div><!--\n<table>\nrow1\nrow2\n</table>\n--><div>bot</div>"
        out = fb._uncomment_html(html)
        assert "<table>" in out and "</table>" in out
        assert "<!--" not in out

    def test_no_comments_returned_as_is(self):
        html = "<div>plain</div>"
        assert fb._uncomment_html(html) == html

    @pytest.mark.parametrize("bad", [None, "", 42])
    def test_invalid_inputs_safe(self, bad):
        out = fb._uncomment_html(bad)
        assert out == "" or out == bad


# =====================================================================
# Team URL resolution
# =====================================================================
class TestResolveFbrefTeamUrl:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("alias", [
        "USA", "United States", "USMNT", "estados unidos", "united states men",
    ])
    async def test_static_mapping_hits_for_usa_aliases(self, alias):
        out = await fb.resolve_fbref_team_url(None, alias)
        assert out["available"] is True
        assert "United-States" in out["url"]
        assert out["source"] == "static_mapping"

    @pytest.mark.asyncio
    async def test_static_mapping_paraguay(self):
        out = await fb.resolve_fbref_team_url(None, "Paraguay")
        assert out["available"] is True
        assert "Paraguay" in out["url"]
        assert out["team_type"] == "national_team"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", ["", None, "   ", 123])
    async def test_missing_team_name_returns_unavailable(self, bad):
        out = await fb.resolve_fbref_team_url(None, bad)
        assert out["available"] is False
        assert fb.RC_TEAM_URL_MISSING in out["reason_codes"]

    @pytest.mark.asyncio
    async def test_unknown_team_returns_unavailable(self):
        out = await fb.resolve_fbref_team_url(None, "Atlantis FC Phantom")
        assert out["available"] is False
        assert fb.RC_TEAM_URL_MISSING in out["reason_codes"]

    @pytest.mark.asyncio
    async def test_mongo_mapping_fallback(self):
        """When the static table misses, a Mongo entry must be used."""
        class FakeColl:
            async def find_one(self, q):
                return {
                    "provider":         "fbref",
                    "team_name_norm":   "kazakhstan",
                    "fbref_team_url":   "https://fbref.com/en/squads/abc123/Kazakhstan",
                    "team_type":        "national_team",
                }
        class FakeDB:
            def __getitem__(self, name):
                return FakeColl()
        out = await fb.resolve_fbref_team_url(None, "Kazakhstan", db=FakeDB())
        assert out["available"] is True
        assert out["source"] == "mongo_mapping"
        assert "Kazakhstan" in out["url"]


# =====================================================================
# parse_fbref_team_html
# =====================================================================
class TestParseFbrefTeamHtml:
    def test_parses_xg_match_logs_happy_path(self):
        html = _build_matchlogs_html(_SAMPLE_ROWS)
        out  = fb.parse_fbref_team_html(html, limit=15)
        assert out["available"] is True
        assert out["xg_available"] is True
        assert out["source"] == "fbref"
        assert len(out["logs"]) == 5
        # Newest-first ordering.
        newest = out["logs"][0]
        assert newest["opponent"] == "Paraguay"
        assert newest["xg_for"]     == 1.72
        assert newest["xg_against"] == 0.91
        assert newest["npxg_for"]   == 1.45
        assert newest["shots_for"]  == 13
        assert newest["sot_for"]    == 5
        assert newest["possession"] == 58
        # Reason codes record availability.
        assert fb.RC_LOGS_AVAILABLE in out["reason_codes"]

    def test_extracts_tables_inside_html_comments(self):
        """FBref hides many tables in comments — the parser must
        un-comment them before parsing."""
        inner = _build_matchlogs_html(_SAMPLE_ROWS[:2])
        html  = _wrap_in_comment(inner)
        out   = fb.parse_fbref_team_html(html, limit=15)
        assert out["available"] is True
        assert out["xg_available"] is True
        assert len(out["logs"]) == 2

    def test_limit_truncates_logs_after_reverse(self):
        html = _build_matchlogs_html(_SAMPLE_ROWS)
        out  = fb.parse_fbref_team_html(html, limit=3)
        # Top 3 newest only.
        assert len(out["logs"]) == 3
        assert [r["opponent"] for r in out["logs"]] == [
            "Paraguay", "Bolivia", "Honduras",
        ]

    def test_missing_xg_columns_flags_reason_code(self):
        """A team-log table that does NOT carry xG must still parse —
        but the reason codes must announce the gap."""
        html = (
            '<table id="matchlogs_for">'
            '<thead><tr>'
            '<th data-stat="date">Date</th>'
            '<th data-stat="opponent">Opp</th>'
            '<th data-stat="shots_for">Sh</th>'
            '</tr></thead>'
            '<tbody>'
            '<tr><td>2026-06-12</td><td>Paraguay</td><td>13</td></tr>'
            '</tbody></table>'
        )
        out = fb.parse_fbref_team_html(html)
        assert out["available"] is True
        assert out["xg_available"] is False
        assert fb.RC_XG_COLUMNS_MISSING in out["reason_codes"]
        assert out["logs"][0]["xg_for"] is None
        assert out["logs"][0]["shots_for"] == 13

    def test_skips_subheader_rows(self):
        """FBref injects ``<tr class="thead">`` separators inside tbody.
        Those rows must not be returned as logs."""
        sep = '<tr class="thead spacer"><td colspan="12">— Friendlies —</td></tr>'
        body = sep + (
            "<tr>"
            "<td>2026-06-12</td><td>Friendlies</td><td>Home</td><td>W</td>"
            "<td>Paraguay</td><td>1.72</td><td>0.91</td><td></td><td></td>"
            "<td>13</td><td>5</td><td>58</td>"
            "</tr>"
        )
        header_cells = (
            '<th data-stat="date">Date</th>'
            '<th data-stat="comp">Comp</th>'
            '<th data-stat="venue">Venue</th>'
            '<th data-stat="result">Result</th>'
            '<th data-stat="opponent">Opp</th>'
            '<th data-stat="xg_for">xG</th>'
            '<th data-stat="xg_against">xGA</th>'
            '<th data-stat="npxg_for">npxG</th>'
            '<th data-stat="npxg_against">npxGA</th>'
            '<th data-stat="shots_for">Sh</th>'
            '<th data-stat="shots_on_target_for">SoT</th>'
            '<th data-stat="possession">Poss</th>'
        )
        html = (
            f'<table id="matchlogs_for">'
            f'<thead><tr>{header_cells}</tr></thead>'
            f'<tbody>{body}</tbody></table>'
        )
        out = fb.parse_fbref_team_html(html)
        assert len(out["logs"]) == 1
        assert out["logs"][0]["opponent"] == "Paraguay"

    def test_no_table_returns_unavailable(self):
        out = fb.parse_fbref_team_html("<html><body><p>no tables</p></body></html>")
        assert out["available"] is False
        assert fb.RC_TABLE_NOT_FOUND in out["reason_codes"]

    @pytest.mark.parametrize("html", [None, "", 0, [], {}])
    def test_invalid_inputs_return_unavailable(self, html):
        out = fb.parse_fbref_team_html(html)  # type: ignore[arg-type]
        assert out["available"] is False

    def test_dashes_and_empty_cells_coerce_to_none(self):
        rows = [
            {"date": "2026-06-12", "comp": "Frd", "venue": "H", "result": "W",
             "opponent": "Paraguay", "xg": "-", "xga": "", "npxg": "N/A",
             "npxga": "—", "sh": "13", "sot": "5", "poss": ""},
        ]
        html = _build_matchlogs_html(rows)
        out  = fb.parse_fbref_team_html(html)
        rec  = out["logs"][0]
        assert rec["xg_for"]       is None
        assert rec["xg_against"]   is None
        assert rec["npxg_for"]     is None
        assert rec["npxg_against"] is None
        assert rec["possession"]   is None
        assert rec["shots_for"]    == 13   # still parsed


# =====================================================================
# fetch_fbref_team_match_logs (network layer)
# =====================================================================
class TestFetchFbrefTeamMatchLogs:
    @pytest.mark.asyncio
    async def test_missing_url_returns_unavailable(self):
        out = await fb.fetch_fbref_team_match_logs(None, "")
        assert out["available"] is False
        assert fb.RC_TEAM_URL_MISSING in out["reason_codes"]

    @pytest.mark.asyncio
    async def test_404_returns_unavailable(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(404, text="not found"),
        )) as c:
            out = await fb.fetch_fbref_team_match_logs(
                c, "https://fbref.com/en/squads/x/y",
            )
        assert out["available"] is False
        assert fb.RC_UNAVAILABLE in out["reason_codes"]
        assert out["source_url"].endswith("/y")

    @pytest.mark.asyncio
    async def test_429_returns_rate_limited(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(429, text="slow down"),
        )) as c:
            out = await fb.fetch_fbref_team_match_logs(
                c, "https://fbref.com/en/squads/x/y",
            )
        assert out["available"] is False
        assert fb.RC_RATE_LIMITED in out["reason_codes"]

    @pytest.mark.asyncio
    async def test_full_round_trip_returns_logs(self):
        html = _build_matchlogs_html(_SAMPLE_ROWS)
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, text=html),
        )) as c:
            out = await fb.fetch_fbref_team_match_logs(
                c, "https://fbref.com/en/squads/6050555d/United-States-Men-Stats",
            )
        assert out["available"] is True
        assert out["xg_available"] is True
        assert len(out["logs"]) == 5
        assert out["logs"][0]["opponent"] == "Paraguay"

    @pytest.mark.asyncio
    async def test_full_round_trip_with_commented_table(self):
        """FBref's real DOM hides many tables in comments. Ensure the
        full fetch → parse pipeline still recovers them."""
        inner = _build_matchlogs_html(_SAMPLE_ROWS[:3])
        html  = f"<html><body><div id=all>{_wrap_in_comment(inner)}</div></body></html>"
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, text=html),
        )) as c:
            out = await fb.fetch_fbref_team_match_logs(
                c, "https://fbref.com/x", limit=15,
            )
        assert out["available"] is True
        assert out["xg_available"] is True
        assert len(out["logs"]) == 3
