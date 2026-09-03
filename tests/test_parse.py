from cfbsicko.parse import map_picks_to_slate, parse_pick_text, parse_slate

WEEK1_SNIPPET = """
Thursday
-   Colorado at Georgia Tech — Georgia Tech -6.5 | O/U 50.5
-   UAB at Illinois — Illinois -27.5 | O/U 54.5
-   Idaho at Utah — Utah -34.5 | O/U 57.5
Friday
-   Indiana State at Purdue — Purdue -36.5 | O/U 57.5
-   Toledo at Michigan State — Michigan State -9.5 | O/U 47.5
Saturday
-   Oregon State at Houston — Houston -20.5 | O/U 49.5
-   Ohio at Nebraska — Nebraska -23.5 | O/U 46.5
-   Ball State at Ohio State — Ohio State -50.5 | O/U 56.5
-   Boston College at Cincinnati — Cincinnati -7.5 | O/U 49.5
-   Texas State at Texas — Texas -29.5 | O/U 59.5
-   Clemson at LSU — LSU -10.5 | O/U 50.5
-   UCLA at California — UCLA -1.5 | O/U 53.5
-   Washington State at Washington — Washington -23.5 | O/U 50.5
Monday
-   SMU at Florida State — Florida State -3.5 | O/U 53.5
"""


def test_parse_slate_home_spread():
    games = parse_slate(WEEK1_SNIPPET)
    by_home = {g.home: g for g in games}
    assert by_home["Georgia Tech"].spread_home == -6.5
    assert by_home["Georgia Tech"].day_label == "Thursday"
    assert by_home["California"].spread_home == 1.5  # UCLA favored on the road
    assert by_home["Florida State"].day_label == "Monday"
    assert len(games) == 14


def test_parse_pick_shapes():
    spread = parse_pick_text("Houston -20.5")
    assert spread is not None
    assert spread.market == "spread"
    assert spread.line == -20.5
    total = parse_pick_text("Purdue/ISU Under 57.5")
    assert total is not None
    assert total.market == "total"
    assert total.side == "under"


def test_map_sheet_shorthand():
    games = parse_slate(WEEK1_SNIPPET)
    report = map_picks_to_slate(
        [
            "Houston -20.5",
            "PUR/ISU Under 57.5",
            "Ohio +23.5",
            "Ohio State -50.5",
            "Washington State +23.5",
            "Cal +1.5",
            "BC +7.5",
            "ILL/UAB Over 57.5",
            "Texas -29.5",
            "SMU -3.5",
        ],
        games,
    )
    assert report.unmapped == []
    by_raw = {m.raw: m for m in report.mapped}
    assert by_raw["Houston -20.5"].side == "home"
    assert by_raw["Ohio +23.5"].side == "away"
    assert games[by_raw["Ohio +23.5"].game_index].away == "Ohio"
    assert games[by_raw["Ohio State -50.5"].game_index].home == "Ohio State"
    assert by_raw["Washington State +23.5"].side == "away"
    assert by_raw["Cal +1.5"].side == "home"
    assert by_raw["ILL/UAB Over 57.5"].market == "total"
    assert any("57.5" in w for w in report.warnings)
