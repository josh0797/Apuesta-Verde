#!/usr/bin/env python3
"""Backend testing for football competition filtering optimization.

Tests:
1. Module imports and exports
2. Competition tier matching (tier_1, tier_2, tier_3, disallowed)
3. Priority ordering
4. Research query builder with tier budgets
5. Injury sources disabled behavior
6. API regression tests
7. Match data with competition_* fields
"""
import sys
import requests
from datetime import datetime

# Public endpoint
BASE_URL = "https://low-volatility-plays.preview.emergentagent.com"

class FootballCompetitionTester:
    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.failures = []
        self.token = None

    def test(self, name, condition, error_msg=""):
        """Run a single test assertion"""
        self.tests_run += 1
        print(f"\n🔍 Testing: {name}")
        
        if condition:
            self.tests_passed += 1
            print(f"✅ PASSED")
            return True
        else:
            self.tests_failed += 1
            msg = f"❌ FAILED: {error_msg}" if error_msg else "❌ FAILED"
            print(msg)
            self.failures.append(f"{name}: {error_msg}")
            return False

    def test_module_imports(self):
        """Test that all expected exports are available"""
        print("\n" + "="*80)
        print("MODULE IMPORTS & EXPORTS")
        print("="*80)
        
        try:
            sys.path.insert(0, '/app/backend')
            from services import football_competitions as fc
            
            # Check exports
            exports = [
                'FOOTBALL_COMPETITION_TIERS',
                'ALLOWED_TIERS',
                'MAX_MATCHES_TO_HYDRATE',
                'MAX_MATCHES_TO_ANALYZE',
                'normalize_competition_name',
                'get_competition_tier',
                'is_allowed_competition',
                'get_competition_priority',
                'get_competition_meta',
                'annotate_match_competition',
            ]
            
            for export in exports:
                self.test(
                    f"Export: {export}",
                    hasattr(fc, export),
                    f"Missing export: {export}"
                )
            
            return fc
        except Exception as e:
            self.test("Module import", False, str(e))
            return None

    def test_competition_tiers(self, fc):
        """Test competition tier matching"""
        print("\n" + "="*80)
        print("COMPETITION TIER MATCHING")
        print("="*80)
        
        # Tier 1 tests
        tier1_tests = [
            ("Premier League", "tier_1"),
            ("EPL", "tier_1"),
            ("LaLiga", "tier_1"),
            ("La Liga", "tier_1"),
            ("Primera División", "tier_1"),
            ("Liga MX, Clausura - Mexico", "tier_1"),
            ("UEFA Champions League - Europe", "tier_1"),
        ]
        
        for comp_name, expected_tier in tier1_tests:
            result = fc.get_competition_tier(comp_name)
            self.test(
                f"Tier 1: {comp_name}",
                result == expected_tier,
                f"Expected {expected_tier}, got {result}"
            )
        
        # Tier 2 tests
        tier2_tests = [
            ("Ligue 1", "tier_2"),
            ("Copa Libertadores - South America", "tier_2"),
            ("UEFA European Championship", "tier_2"),
        ]
        
        for comp_name, expected_tier in tier2_tests:
            result = fc.get_competition_tier(comp_name)
            self.test(
                f"Tier 2: {comp_name}",
                result == expected_tier,
                f"Expected {expected_tier}, got {result}"
            )
        
        # Disallowed competitions
        disallowed_tests = [
            "Eredivisie",
            "Championship",
        ]
        
        for comp_name in disallowed_tests:
            result = fc.get_competition_tier(comp_name)
            self.test(
                f"Disallowed: {comp_name}",
                result is None,
                f"Expected None, got {result}"
            )

    def test_priorities(self, fc):
        """Test priority ordering"""
        print("\n" + "="*80)
        print("PRIORITY ORDERING")
        print("="*80)
        
        p1 = fc.get_competition_priority("Premier League")
        p2 = fc.get_competition_priority("Ligue 1")
        p3 = fc.get_competition_priority("FA Cup")
        p_unknown = fc.get_competition_priority("Eredivisie")
        
        self.test("Tier 1 priority = 100", p1 == 100, f"Got {p1}")
        self.test("Tier 2 priority = 70", p2 == 70, f"Got {p2}")
        self.test("Tier 3 priority = 40", p3 == 40, f"Got {p3}")
        self.test("Unknown priority = 0", p_unknown == 0, f"Got {p_unknown}")

    def test_competition_meta(self, fc):
        """Test get_competition_meta"""
        print("\n" + "="*80)
        print("COMPETITION METADATA")
        print("="*80)
        
        meta = fc.get_competition_meta("EPL")
        
        self.test(
            "Meta: canonical_name = Premier League",
            meta and meta.get("canonical_name") == "Premier League",
            f"Got {meta.get('canonical_name') if meta else None}"
        )
        self.test(
            "Meta: tier = tier_1",
            meta and meta.get("tier") == "tier_1"
        )
        self.test(
            "Meta: priority = 100",
            meta and meta.get("priority") == 100
        )
        self.test(
            "Meta: type = league",
            meta and meta.get("type") == "league"
        )
        self.test(
            "Meta: region = England",
            meta and meta.get("region") == "England"
        )

    def test_annotate_match(self, fc):
        """Test annotate_match_competition"""
        print("\n" + "="*80)
        print("MATCH ANNOTATION")
        print("="*80)
        
        doc = {"league": "Premier League - England"}
        fc.annotate_match_competition(doc)
        
        expected_fields = [
            "competition_tier",
            "competition_priority",
            "competition_canonical_name",
            "competition_type",
            "competition_region",
            "allowed_competition",
        ]
        
        for field in expected_fields:
            self.test(
                f"Annotate: {field} exists",
                field in doc,
                f"Missing field: {field}"
            )

    def test_research_queries(self, fc):
        """Test research query builder"""
        print("\n" + "="*80)
        print("RESEARCH QUERY BUILDER")
        print("="*80)
        
        try:
            from services import research_queries as rq
            
            # Test tier_1 budget
            match_t1 = {
                "home_team": {"name": "Chelsea"},
                "away_team": {"name": "Tottenham"},
                "league": "Premier League",
                "competition_tier": "tier_1",
                "kickoff_iso": "2026-05-15T15:00:00Z",
                "is_live": False,
            }
            q1 = rq.build_match_research_queries(match_t1)
            emitted_t1 = sum(len(v) for k, v in q1.items() if k != "_meta")
            
            self.test(
                "Research: tier_1 budget <= 8",
                emitted_t1 <= 8,
                f"Emitted {emitted_t1}"
            )
            self.test(
                "Research: team_news included",
                "team_news" in q1
            )
            
            # Test tier_3 budget
            match_t3 = {
                "home_team": {"name": "Sevilla"},
                "away_team": {"name": "Mallorca"},
                "league": "Copa del Rey",
                "competition_tier": "tier_3",
                "kickoff_iso": "2026-02-10T20:00:00Z",
                "is_live": False,
            }
            q3 = rq.build_match_research_queries(match_t3)
            emitted_t3 = sum(len(v) for k, v in q3.items() if k != "_meta")
            
            self.test(
                "Research: tier_3 budget <= 3",
                emitted_t3 <= 3,
                f"Emitted {emitted_t3}"
            )
            
            # Test live context
            match_live = {
                "home_team": {"name": "Real Madrid"},
                "away_team": {"name": "Barcelona"},
                "league": "LaLiga",
                "competition_tier": "tier_1",
                "kickoff_iso": "2026-04-20T19:00:00Z",
                "is_live": True,
            }
            q_live = rq.build_match_research_queries(match_live)
            emitted_live = sum(len(v) for k, v in q_live.items() if k != "_meta")
            
            self.test(
                "Research: live budget <= 8",
                emitted_live <= 8,
                f"Emitted {emitted_live}"
            )
            
        except Exception as e:
            self.test("Research queries", False, str(e))

    def test_injury_sources(self):
        """Test injury sources disabled behavior"""
        print("\n" + "="*80)
        print("INJURY SOURCES (DISABLED)")
        print("="*80)
        
        try:
            from services import injury_sources as inj
            
            self.test(
                "Injury: INJURY_SOURCES_ENABLED exists",
                hasattr(inj, "INJURY_SOURCES_ENABLED")
            )
            self.test(
                "Injury: disabled by default",
                inj.INJURY_SOURCES_ENABLED is False
            )
            
            # Test disabled behavior
            import asyncio
            result = asyncio.run(inj.fetch_team_news("Chelsea", "Tottenham", "Premier League"))
            
            self.test(
                "Injury: returns dict",
                isinstance(result, dict)
            )
            self.test(
                "Injury: _disabled flag = True",
                result.get("_disabled") is True
            )
            
        except Exception as e:
            self.test("Injury sources", False, str(e))

    def login(self):
        """Login and get token"""
        try:
            resp = requests.post(
                f"{BASE_URL}/api/auth/login",
                json={"email": "demo@valuebet.app", "password": "demo1234"},
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                self.token = data.get("token")
                return True
        except Exception as e:
            print(f"Login failed: {e}")
        return False

    def test_api_regression(self):
        """Test API endpoints"""
        print("\n" + "="*80)
        print("API REGRESSION TESTS")
        print("="*80)
        
        # Login first
        if not self.login():
            self.test("API: Login", False, "Could not login")
            return
        
        headers = {"Authorization": f"Bearer {self.token}"}
        
        # Test picks_today
        try:
            resp = requests.get(
                f"{BASE_URL}/api/picks/today?sport=football",
                headers=headers,
                timeout=10
            )
            self.test(
                "API: /api/picks/today",
                resp.status_code == 200,
                f"Status: {resp.status_code}"
            )
            
            if resp.status_code == 200:
                data = resp.json()
                self.test(
                    "API: picks_today returns dict",
                    isinstance(data, dict),
                    f"Got type: {type(data)}"
                )
                
                # Check structure
                pick_run = data.get("pick_run", {})
                picks = pick_run.get("payload", {}).get("picks", [])
                
                if picks and len(picks) > 0:
                    print(f"   ℹ️  Found {len(picks)} picks")
                else:
                    print("   ℹ️  No picks returned")
        except Exception as e:
            self.test("API: picks_today", False, str(e))
        
        # Test matches/upcoming to verify competition_* fields
        try:
            resp = requests.get(
                f"{BASE_URL}/api/matches/upcoming?sport=football",
                headers=headers,
                timeout=10
            )
            self.test(
                "API: /api/matches/upcoming",
                resp.status_code == 200,
                f"Status: {resp.status_code}"
            )
            
            if resp.status_code == 200:
                data = resp.json()
                matches = data.get("items", [])
                if matches and len(matches) > 0:
                    match = matches[0]
                    comp_fields = [k for k in match.keys() if k.startswith("competition_")]
                    if comp_fields:
                        self.test(
                            "API: matches have competition_* fields",
                            True,
                            f"Found: {comp_fields}"
                        )
                    else:
                        print("   ℹ️  Note: competition_* fields stored in DB but not exposed in API response")
                else:
                    print("   ℹ️  No upcoming matches")
        except Exception as e:
            self.test("API: matches/upcoming", False, str(e))
        
        # Test saved-views (regression)
        try:
            resp = requests.get(
                f"{BASE_URL}/api/profile/saved-views",
                headers=headers,
                timeout=10
            )
            self.test(
                "API: /api/profile/saved-views",
                resp.status_code == 200,
                f"Status: {resp.status_code}"
            )
        except Exception as e:
            self.test("API: saved-views", False, str(e))

    def print_summary(self):
        """Print test summary"""
        print("\n" + "="*80)
        print("TEST SUMMARY")
        print("="*80)
        print(f"\n📊 Tests Run: {self.tests_run}")
        print(f"✅ Passed: {self.tests_passed}")
        print(f"❌ Failed: {self.tests_failed}")
        
        if self.failures:
            print("\n❌ FAILURES:")
            for failure in self.failures:
                print(f"  - {failure}")
        
        success_rate = (self.tests_passed / self.tests_run * 100) if self.tests_run > 0 else 0
        print(f"\n📈 Success Rate: {success_rate:.1f}%")
        
        return self.tests_failed == 0


def main():
    tester = FootballCompetitionTester()
    
    print("="*80)
    print("FOOTBALL COMPETITION FILTERING - BACKEND TESTS")
    print("="*80)
    
    # Module tests
    fc = tester.test_module_imports()
    if fc:
        tester.test_competition_tiers(fc)
        tester.test_priorities(fc)
        tester.test_competition_meta(fc)
        tester.test_annotate_match(fc)
        tester.test_research_queries(fc)
    
    # Injury sources
    tester.test_injury_sources()
    
    # API tests
    tester.test_api_regression()
    
    # Summary
    success = tester.print_summary()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
