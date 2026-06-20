"""One-shot helper to download + parse Copa América 2024 from openfootball.

Source: https://raw.githubusercontent.com/openfootball/copa-america/master/2024--usa/copa.txt
Output: /app/data/openfootball/copa_america_2024.json

Schema produced matches wc2022.json / euro2024.json:
  {"name": "Copa América 2024",
   "matches": [{"round": "Matchday X" | "Quarter-finals" | ...,
                "date": "YYYY-MM-DD",
                "time": "HH:MM",
                "team1": "...",
                "team2": "...",
                "score": {"ft": [h, a], "ht": [None, None]},
                "group": "Group A" | None,
                "ground": "..."}, ...]}
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

URL = ("https://raw.githubusercontent.com/openfootball/copa-america/"
        "master/2024--usa/copa.txt")
OUT = Path("/app/data/openfootball/copa_america_2024.json")

DAY_NAMES = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
          "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}

# Match line regex. Two cases handled separately:
#  1) Regular: "Thu Jun 20 20:00 UTC-4   Argentina      2-0   Canada  @ ..."
#  2) Knockout: "Thu Jul 4 20:00 UTC-5   Argentina  4-2 pen. (1-1) Ecuador @ ..."
#     The score BEFORE 'pen.'/'a.e.t.' is the post-90 total (penalty/ET),
#     the score in parens is the regulation 90' score → that is the h2h
#     market settlement reference. We store ``ft = (X, Y)`` from the
#     paren score, and ``p`` / ``et`` from the front score.
MATCH_KNOCKOUT_RE = re.compile(
    r"^(?P<dow>Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
    r"(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
    r"(?P<day>\d+)\s+"
    r"(?P<time>\d{2}:\d{2})\s+UTC[-+]?\d+\s+"
    r"(?P<team1>.+?)\s+(?P<sh_total>\d+)\s*-\s*(?P<sa_total>\d+)\s+"
    r"(?P<kind>pen\.|a\.e\.t\.|aet)\s*"
    r"\((?P<sh_reg>\d+)\s*-\s*(?P<sa_reg>\d+)\)\s+"
    r"(?P<team2>.+?)\s+@\s+(?P<ground>.+?)(?:\s+#.*)?\s*$"
)
MATCH_REGULAR_RE = re.compile(
    r"^(?P<dow>Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
    r"(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
    r"(?P<day>\d+)\s+"
    r"(?P<time>\d{2}:\d{2})\s+UTC[-+]?\d+\s+"
    r"(?P<team1>.+?)\s+(?P<sh>\d+)\s*-\s*(?P<sa>\d+)\s+"
    r"(?P<team2>.+?)\s+@\s+(?P<ground>.+?)(?:\s+#.*)?\s*$"
)

# Group lines, e.g. "Group A  |  Argentina       Peru       Chile      Canada"
GROUP_RE = re.compile(r"^Group\s+(?P<letter>[A-Z])\s*\|\s*(?P<teams>.+?)\s*$")
# Round headers, e.g. "▪ Group A", "▪ Quarter-finals", "▪ Semi-finals", "▪ Final", "▪ Third Place"
ROUND_RE = re.compile(r"^[▪•·●]\s*(?P<name>.+?)\s*$")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())


def parse(text: str) -> dict:
    matches: list[dict] = []
    team_to_group: dict[str, str] = {}
    current_round = "Group Stage"
    current_group = None
    # Track date so we can normalize even when the line has only "Thu Jun 20"
    year = 2024

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        # Group declaration (the table at the top).
        m = GROUP_RE.match(line)
        if m:
            letter = m.group("letter")
            teams = re.split(r"\s{2,}", _norm(m.group("teams")))
            for t in teams:
                team_to_group[_norm(t)] = f"Group {letter}"
            continue
        # Round / phase header line.
        m = ROUND_RE.match(line.strip())
        if m:
            nm = _norm(m.group("name"))
            # Recognized phase names; "Group A" headers we reuse the letter.
            if nm.lower().startswith("group "):
                current_group = nm
                current_round = "Group Stage"
            elif nm.lower().startswith("matchday"):
                current_round = nm
            elif nm.lower().startswith("quarter") or nm.lower().startswith("semi"):
                current_round = ("Quarter-finals" if nm.lower().startswith("quarter")
                                  else "Semi-finals")
                current_group = None
            elif nm.lower().startswith("third"):
                current_round = "Third place play-off"
                current_group = None
            elif nm.lower().startswith("final") or nm.lower() == "final":
                current_round = "Final"
                current_group = None
            continue
        # Match line (try knockout suffix first, then regular).
        m = MATCH_KNOCKOUT_RE.match(line.lstrip())
        is_knockout = bool(m)
        if not m:
            m = MATCH_REGULAR_RE.match(line.lstrip())
        if m:
            mon = MONTHS[m.group("mon")]
            day = int(m.group("day"))
            try:
                d = datetime(year, mon, day).strftime("%Y-%m-%d")
            except ValueError:
                continue
            t1 = _norm(m.group("team1"))
            t2 = _norm(m.group("team2"))
            grp = team_to_group.get(t1) or team_to_group.get(t2) or current_group

            if is_knockout:
                # Regulation 90' = paren score (used for h2h DRAW settlement).
                sh_reg = int(m.group("sh_reg"))
                sa_reg = int(m.group("sa_reg"))
                sh_total = int(m.group("sh_total"))
                sa_total = int(m.group("sa_total"))
                kind = m.group("kind")
                score: dict = {"ft": [sh_reg, sa_reg],
                                "ht": [None, None]}
                if kind in ("pen.",):
                    score["p"] = [sh_total, sa_total]
                else:  # a.e.t.
                    score["et"] = [sh_total, sa_total]
            else:
                score = {"ft": [int(m.group("sh")), int(m.group("sa"))],
                          "ht": [None, None]}

            matches.append({
                "round":  current_round,
                "date":   d,
                "time":   m.group("time"),
                "team1":  t1,
                "team2":  t2,
                "score":  score,
                "group":  grp if (current_round == "Group Stage") else None,
                "ground": _norm(m.group("ground")),
            })
    return {"name": "Copa América 2024", "matches": matches}


def main():
    print(f"[download] {URL}")
    with urllib.request.urlopen(URL, timeout=20) as r:
        txt = r.read().decode("utf-8")
    print(f"[download] OK ({len(txt)} bytes)")
    doc = parse(txt)
    print(f"[parse] {len(doc['matches'])} matches extracted")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    print(f"[write] {OUT}")
    # Sanity: print first 3 + last 3.
    for m in doc["matches"][:3] + doc["matches"][-3:]:
        print(f"  {m['date']} {m['time']} | {m['team1']:20} {m['score']['ft'][0]}-{m['score']['ft'][1]} {m['team2']:20} | {m['round']:20} | {m['group']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
