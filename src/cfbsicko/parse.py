"""Parse commissioner slate emails and free-text Week 1 sheet picks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from cfbsicko.rules import Market, Side

DAY_HEADINGS = ("Thursday", "Friday", "Saturday", "Sunday", "Monday")

SLATE_LINE = re.compile(
    r"^(?P<away>.+?)\s+at\s+(?P<home>.+?)\s+[—-]\s+(?P<fav>.+?)\s+"
    r"(?P<spread>[+-]?\d+(?:\.\d+)?)\s*\|\s*O/U\s+(?P<total>\d+(?:\.\d+)?)\s*$",
    re.IGNORECASE,
)
SPREAD_PICK = re.compile(
    r"^(?P<team>.+?)\s+(?P<line>[+-]\d+(?:\.\d+)?)\s*$",
)
TOTAL_PICK = re.compile(
    r"^(?P<teams>.+?)\s+(?P<side>Over|Under)\s+(?P<line>\d+(?:\.\d+)?)\s*$",
    re.IGNORECASE,
)

# Short tokens that appear in the Week 1 sheet. Values are substrings of official names.
TEAM_ALIASES: dict[str, tuple[str, ...]] = {
    "purdue": ("Purdue",),
    "pur": ("Purdue",),
    "isu": ("Indiana State", "Iowa State"),
    "smu": ("SMU",),
    "kentucky": ("Kentucky",),
    "memphis": ("Memphis",),
    "syracuse": ("Syracuse",),
    "houston": ("Houston",),
    "oklahoma": ("Oklahoma",),
    "bc": ("Boston College",),
    "boston college": ("Boston College",),
    "lsu": ("LSU",),
    "clemson": ("Clemson",),
    "ucla": ("UCLA",),
    "cal": ("California",),
    "california": ("California",),
    "msu": ("Michigan State",),
    "michigan state": ("Michigan State",),
    "northern illinois": ("Northern Illinois",),
    "vmi": ("VMI",),
    "osu": ("Ohio State", "Oklahoma State"),
    "ohio state": ("Ohio State",),
    "ball state": ("Ball State",),
    "nd": ("Notre Dame",),
    "notre dame": ("Notre Dame",),
    "akron": ("Akron",),
    "wake": ("Wake Forest",),
    "wake forest": ("Wake Forest",),
    "ole miss": ("Ole Miss",),
    "louisville": ("Louisville",),
    "toledo": ("Toledo",),
    "alabama": ("Alabama",),
    "georgia tech": ("Georgia Tech",),
    "idaho": ("Idaho",),
    "utah": ("Utah",),
    "fresno state": ("Fresno State",),
    "usc": ("USC",),
    "ohio": ("Ohio",),
    "north alabama": ("North Alabama",),
    "texas": ("Texas",),
    "washington state": ("Washington State",),
    "ecu": ("East Carolina",),
    "east carolina": ("East Carolina",),
    "cincinnati": ("Cincinnati",),
    "north texas": ("North Texas",),
    "wisconsin": ("Wisconsin",),
    "west virginia": ("West Virginia",),
    "ill": ("Illinois",),
    "illinois": ("Illinois",),
    "uab": ("UAB",),
    "ndsu": ("North Dakota State",),
    "miami": ("Miami (FL)",),
    "pitt": ("Pittsburgh",),
    "pittsburgh": ("Pittsburgh",),
    "colorado": ("Colorado",),
    "auburn": ("Auburn",),
    "boise state": ("Boise State",),
}


@dataclass(frozen=True)
class SlateGame:
    away: str
    home: str
    favorite: str
    spread: float  # favorite's listed number (negative in the email)
    total: float
    day_label: str

    @property
    def spread_home(self) -> float:
        """Home-perspective spread. Email lists the favorite."""
        listed = -abs(self.spread)
        if _token_hits(self.favorite, (_norm(self.home),)):
            return listed
        if _token_hits(self.favorite, (_norm(self.away),)):
            return abs(self.spread)
        raise ValueError(f"Favorite {self.favorite!r} is neither {self.home} nor {self.away}")


@dataclass(frozen=True)
class ParsedPick:
    raw: str
    market: Market
    side: Side
    tokens: tuple[str, ...]
    line: float


@dataclass(frozen=True)
class MappedPick:
    raw: str
    game_index: int
    market: Market
    side: Side
    warnings: tuple[str, ...] = ()


@dataclass
class MapReport:
    mapped: list[MappedPick] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_slate(text: str) -> list[SlateGame]:
    games: list[SlateGame] = []
    day = "Saturday"
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("-").strip()
        if not line:
            continue
        heading = line.rstrip(":").title()
        if heading in DAY_HEADINGS:
            day = heading
            continue
        match = SLATE_LINE.match(line)
        if not match:
            continue
        games.append(
            SlateGame(
                away=_clean_team(match.group("away")),
                home=_clean_team(match.group("home")),
                favorite=_clean_team(match.group("fav")),
                spread=float(match.group("spread")),
                total=float(match.group("total")),
                day_label=day,
            )
        )
    return games


def parse_pick_text(raw: str) -> ParsedPick | None:
    text = " ".join(raw.strip().split())
    if not text:
        return None
    total = TOTAL_PICK.match(text)
    if total:
        side: Side = "over" if total.group("side").lower() == "over" else "under"
        tokens = tuple(_split_team_tokens(total.group("teams")))
        return ParsedPick(raw=text, market="total", side=side, tokens=tokens, line=float(total.group("line")))
    spread = SPREAD_PICK.match(text)
    if spread:
        return ParsedPick(
            raw=text,
            market="spread",
            side="home",  # resolved against the matched game
            tokens=(_clean_team(spread.group("team")),),
            line=float(spread.group("line")),
        )
    return None


def map_picks_to_slate(raw_picks: list[str], games: list[SlateGame]) -> MapReport:
    report = MapReport()
    for raw in raw_picks:
        text = raw.strip()
        if not text:
            continue
        parsed = parse_pick_text(text)
        if parsed is None:
            report.unmapped.append(text)
            continue
        mapped = _resolve_pick(parsed, games)
        if mapped is None:
            report.unmapped.append(text)
            continue
        report.mapped.append(mapped)
        report.warnings.extend(mapped.warnings)
    return report


def _resolve_pick(parsed: ParsedPick, games: list[SlateGame]) -> MappedPick | None:
    candidates: list[tuple[int, int, list[str]]] = []
    for idx, game in enumerate(games):
        score, warns = _score_game(parsed, game)
        if score > 0:
            candidates.append((score, idx, warns))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    best_score, best_idx, warns = candidates[0]
    if len(candidates) > 1 and candidates[1][0] == best_score:
        return None
    game = games[best_idx]
    if parsed.market == "spread":
        side = _spread_side(parsed, game)
        if side is None:
            return None
        if abs(abs(parsed.line) - abs(game.spread_home)) > 0.05:
            warns.append(f"{parsed.raw}: sheet line {parsed.line} vs frozen {game.spread_home}")
        return MappedPick(
            raw=parsed.raw, game_index=best_idx, market="spread", side=side, warnings=tuple(warns)
        )
    if abs(parsed.line - game.total) > 0.05:
        warns.append(f"{parsed.raw}: sheet total {parsed.line} vs frozen {game.total}")
    return MappedPick(
        raw=parsed.raw,
        game_index=best_idx,
        market="total",
        side=parsed.side,
        warnings=tuple(warns),
    )


def _score_game(parsed: ParsedPick, game: SlateGame) -> tuple[int, list[str]]:
    names = (_norm(game.away), _norm(game.home))
    hits = 0
    for token in parsed.tokens:
        if _token_hits(token, names):
            hits += 1
    if parsed.market == "total":
        return (hits, []) if hits >= min(2, len(parsed.tokens)) and hits >= 1 else (0, [])
    return (hits, []) if hits >= 1 else (0, [])


def _token_hits(token: str, names: tuple[str, ...]) -> bool:
    needle = _norm(token)
    aliases = TEAM_ALIASES.get(needle, (token,))
    for alias in aliases:
        alias_n = _norm(alias)
        for name in names:
            if _names_match(alias_n, name):
                return True
    return False


def _names_match(alias_n: str, name: str) -> bool:
    if alias_n == name:
        return True
    aw, nw = alias_n.split(), name.split()
    if not aw or not nw:
        return False
    # Abbreviations already expanded via TEAM_ALIASES. Require exact or full alias words.
    if set(aw) <= set(nw) and len(aw) >= 2:
        return True
    return set(nw) <= set(aw) and len(nw) >= 2 and len(aw) >= 2


def _spread_side(parsed: ParsedPick, game: SlateGame) -> Side | None:
    token = parsed.tokens[0]
    names = (_norm(game.away), _norm(game.home))
    if not _token_hits(token, names):
        return None
    away_n, home_n = names
    aliases = TEAM_ALIASES.get(_norm(token), (token,))
    for alias in aliases:
        alias_n = _norm(alias)
        if _names_match(alias_n, home_n):
            return "home"
        if _names_match(alias_n, away_n):
            return "away"
    # Fall back: listed sign vs home spread.
    if parsed.line < 0:
        return "home" if game.spread_home < 0 else "away"
    return "away" if game.spread_home < 0 else "home"


def _split_team_tokens(raw: str) -> list[str]:
    parts = re.split(r"\s*/\s*", raw.strip())
    return [_clean_team(part) for part in parts if part.strip()]


def _clean_team(value: str) -> str:
    return " ".join(value.replace("—", "-").split())


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def favorite_label(game: SlateGame) -> str:
    if game.spread_home < 0:
        return f"{game.home} {game.spread_home}"
    if game.spread_home > 0:
        return f"{game.away} {-game.spread_home}"
    return f"{game.home} PK"
