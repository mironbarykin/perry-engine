"""Isolated parser for basketball-bund.net.

Fetches league metadata and game schedules from basketball-bund.net,
persists them in Perry's SQLite database, and exposes filtering capabilities
for LLM tool integration.
"""

import json
import re
import sqlite3
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser

from app.storage import _now, connection

import http.cookiejar

BASE_URL = "https://www.basketball-bund.net"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PerryEngine/1.0"
CACHE_TTL_HOURS = 2

_cookie_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cookie_jar))


class RowParser(HTMLParser):
    """Flattens top-level HTML rows into cell dictionaries, preserving text from nested tables."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[dict]] = []
        self._current_row: list[dict] | None = None
        self._current_cell: dict | None = None
        self._table_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        attr_dict = {k.lower(): v for k, v in attrs if v is not None}

        if tag_lower == "table":
            self._table_depth += 1
        elif tag_lower == "tr":
            if self._table_depth == 1:
                self._current_row = []
                self.rows.append(self._current_row)
        elif tag_lower in ("td", "th"):
            if self._table_depth == 1 and self._current_row is not None:
                self._current_cell = {"text": "", "links": [], "class": attr_dict.get("class", "")}
                self._current_row.append(self._current_cell)
        elif tag_lower == "a" and self._current_cell is not None:
            href = attr_dict.get("href")
            if href:
                self._current_cell["links"].append(href)

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower == "table":
            self._table_depth = max(0, self._table_depth - 1)
        elif tag_lower in ("td", "th"):
            if self._table_depth == 1:
                self._current_cell = None
        elif tag_lower == "tr":
            if self._table_depth == 1:
                self._current_row = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell["text"] += data


def fetch_html(url: str) -> str:
    """Fetch HTML content from basketball-bund.net maintaining session cookies for pagination."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with _opener.open(req, timeout=15) as resp:
        content_bytes = resp.read()
        try:
            return content_bytes.decode("iso-8859-1")
        except UnicodeDecodeError:
            return content_bytes.decode("utf-8", errors="replace")


def _extract_short_name(liganame: str, klasse: str, altersklasse: str, geschlecht: str) -> str:
    """Extract or construct a short abbreviation for a league (e.g. BBL, ProA, ProB, NBBL, JBBL, 1RLH)."""
    lname = liganame.strip()

    # Bundesliga & Youth Bundesliga clean short names
    if "easyCredit BBL" in lname or "1. Bundesliga (easyCredit" in lname:
        return "BBL"
    if "Toyota DBBL" in lname or "1. Bundesliga (Toyota DBBL)" in lname:
        return "DBBL"
    if "ProA" in lname:
        return "ProA"
    if "ProB Nord" in lname:
        return "ProB-Nord"
    if "ProB Süd" in lname or "ProB Sued" in lname:
        return "ProB-Sued"
    if "2. DBBL Nord" in lname:
        return "2DBBL-Nord"
    if "2. DBBL Süd" in lname or "2. DBBL Sued" in lname:
        return "2DBBL-Sued"
    if "NBBL" in lname:
        return "NBBL"
    if "JBBL" in lname:
        return "JBBL"

    match = re.search(r"\(([^)]+)\)", lname)
    if match and len(match.group(1).strip()) <= 12:
        return match.group(1).strip()

    # Fallback derivation
    parts = []
    if "1.Regionalliga" in klasse or "1. Regionalliga" in liganame:
        parts.append("1RL")
    elif "2.Regionalliga" in klasse or "2. Regionalliga" in liganame:
        parts.append("2RL")
    elif "Oberliga" in klasse or "Oberliga" in liganame:
        parts.append("OL")
    elif "Landesliga" in klasse or "Landesliga" in liganame:
        parts.append("LL")
    elif "Bezirksliga" in klasse or "Bezirksliga" in liganame:
        parts.append("BL")
    elif "Kreisliga" in klasse or "Kreisliga" in liganame:
        parts.append("KL")
    else:
        parts.append(re.sub(r"[^A-Za-z0-9]", "", klasse)[:4].upper() or "LIGA")

    if altersklasse and altersklasse != "Senioren":
        parts.append(re.sub(r"[^A-Za-z0-9]", "", altersklasse).upper())

    if geschlecht == "männlich":
        parts.append("M" if "U" in altersklasse else "H")
    elif geschlecht == "weiblich":
        parts.append("W" if "U" in altersklasse else "D")

    return "".join(parts)


def parse_leagues_page(html: str, verband_id: int = 11) -> list[dict]:
    """Parse a single page of leagues table (Action=100)."""
    parser = RowParser()
    parser.feed(html)

    leagues = []
    for row in parser.rows:
        if len(row) < 8:
            continue

        # Check if row is a valid data row (sportItemEven / sportItemOdd)
        row_classes = " ".join(cell["class"] for cell in row)
        if "sportItem" not in row_classes:
            continue

        klasse = row[0]["text"].strip()
        altersklasse = row[1]["text"].strip()
        geschlecht = row[2]["text"].strip()
        bezirk = row[3]["text"].strip()
        kreis = row[4]["text"].strip()
        liganame = row[5]["text"].strip()
        liganr = row[6]["text"].strip()

        # Extract liga_id from links in 8th column
        ansicht_cell = row[7]
        liga_id = None
        for href in ansicht_cell["links"]:
            match = re.search(r"liga_id=(\d+)", href)
            if match:
                liga_id = int(match.group(1))
                break

        if not liga_id:
            continue

        short_name = _extract_short_name(liganame, klasse, altersklasse, geschlecht)

        leagues.append(
            {
                "liga_id": liga_id,
                "verband_id": verband_id,
                "klasse": klasse,
                "altersklasse": altersklasse,
                "geschlecht": geschlecht,
                "bezirk": bezirk,
                "kreis": kreis,
                "liganame": liganame,
                "liganr": liganr,
                "short_name": short_name,
            }
        )

    return leagues


def parse_total_league_pages(html: str) -> int:
    """Extract total pages or highest startrow from footer navigation."""
    match = re.search(r"startrow=(\d+)&", html)
    matches = [int(m) for m in re.findall(r"startrow=(\d+)", html)]
    if matches:
        return max(matches)
    return 0


def sync_leagues_to_db(verband_id: int = 11) -> int:
    """Fetch and update all leagues for a given federation (Action=100) into SQLite."""
    startrow = 0
    all_leagues = []
    now = _now()

    while True:
        url = f"{BASE_URL}/index.jsp?Action=100&Verband={verband_id}&startrow={startrow}&viewid="
        html = fetch_html(url)
        page_leagues = parse_leagues_page(html, verband_id)
        if not page_leagues:
            break

        all_leagues.extend(page_leagues)
        max_startrow = parse_total_league_pages(html)
        if startrow >= max_startrow:
            break

        startrow += 10

    if all_leagues:
        with connection() as db:
            for item in all_leagues:
                db.execute(
                    """
                    INSERT INTO basketball_leagues (
                        liga_id, verband_id, klasse, altersklasse, geschlecht,
                        bezirk, kreis, liganame, liganr, short_name, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(liga_id) DO UPDATE SET
                        verband_id = excluded.verband_id,
                        klasse = excluded.klasse,
                        altersklasse = excluded.altersklasse,
                        geschlecht = excluded.geschlecht,
                        bezirk = excluded.bezirk,
                        kreis = excluded.kreis,
                        liganame = excluded.liganame,
                        liganr = excluded.liganr,
                        short_name = excluded.short_name,
                        updated_at = excluded.updated_at
                    """,
                    (
                        item["liga_id"],
                        item["verband_id"],
                        item["klasse"],
                        item["altersklasse"],
                        item["geschlecht"],
                        item["bezirk"],
                        item["kreis"],
                        item["liganame"],
                        item["liganr"],
                        item["short_name"],
                        now,
                    ),
                )

    return len(all_leagues)


def _format_date(date_str: str) -> str:
    """Convert '18.09.2026 20:30' -> ISO '2026-09-18T20:30:00'."""
    date_str = date_str.strip()
    match = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})$", date_str)
    if match:
        day, month, year, hour, minute = match.groups()
        return f"{year}-{month}-{day}T{hour}:{minute}:00"

    # Match date without time
    match_d = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", date_str)
    if match_d:
        day, month, year = match_d.groups()
        return f"{year}-{month}-{day}T00:00:00"

    return date_str


def parse_printable_spielplan(html: str, liga_id: int) -> list[dict]:
    """Parse game list from printable unpaginated schedule (public/spielplan_list.jsp?print=1)."""
    parser = RowParser()
    parser.feed(html)

    games = []
    for row in parser.rows:
        if len(row) < 7:
            continue

        row_classes = " ".join(cell["class"] for cell in row)
        if "sportItem" not in row_classes:
            continue

        try:
            spiel_nr_text = row[0]["text"].strip()
            spiel_nr = int(re.sub(r"\D", "", spiel_nr_text))
        except ValueError:
            continue

        try:
            spieltag_text = row[1]["text"].strip()
            spieltag = int(re.sub(r"\D", "", spieltag_text))
        except ValueError:
            spieltag = 0

        raw_datum = row[2]["text"].strip()
        datum_iso = _format_date(raw_datum)

        heim = row[3]["text"].strip()
        gast = row[4]["text"].strip()
        spielhalle = re.sub(r"\s+", " ", row[5]["text"]).strip()
        schiedsrichter = re.sub(r"\s+", " ", row[6]["text"]).strip()

        games.append(
            {
                "liga_id": liga_id,
                "spiel_nr": spiel_nr,
                "spieltag": spieltag,
                "datum": datum_iso,
                "heim": heim,
                "gast": gast,
                "spielhalle": spielhalle,
                "schiedsrichter": schiedsrichter,
            }
        )

    return games


def sync_schedule_to_db(liga_id: int) -> int:
    """Fetch unpaginated printable game schedule for a league and persist into SQLite."""
    url = f"{BASE_URL}/public/spielplan_list.jsp?print=1&liga_id={liga_id}"
    html = fetch_html(url)
    games = parse_printable_spielplan(html, liga_id)
    now = _now()

    with connection() as db:
        for game in games:
            db.execute(
                """
                INSERT INTO basketball_games (
                    liga_id, spiel_nr, spieltag, datum, heim, gast, spielhalle, schiedsrichter, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(liga_id, spiel_nr) DO UPDATE SET
                    spieltag = excluded.spieltag,
                    datum = excluded.datum,
                    heim = excluded.heim,
                    gast = excluded.gast,
                    spielhalle = excluded.spielhalle,
                    schiedsrichter = excluded.schiedsrichter,
                    updated_at = excluded.updated_at
                """,
                (
                    game["liga_id"],
                    game["spiel_nr"],
                    game["spieltag"],
                    game["datum"],
                    game["heim"],
                    game["gast"],
                    game["spielhalle"],
                    game["schiedsrichter"],
                    now,
                ),
            )

        db.execute(
            """
            INSERT INTO basketball_league_sync (liga_id, last_synced_at)
            VALUES (?, ?)
            ON CONFLICT(liga_id) DO UPDATE SET last_synced_at = excluded.last_synced_at
            """,
            (liga_id, now),
        )

    return len(games)


def _ensure_leagues_synced(verband_id: int | None = None) -> None:
    """Ensure league catalog is populated in DB for specified or default federations."""
    with connection() as db:
        if verband_id is not None:
            count = db.execute(
                "SELECT COUNT(*) FROM basketball_leagues WHERE verband_id = ?", (verband_id,)
            ).fetchone()[0]
            if count == 0:
                sync_leagues_to_db(verband_id)
        else:
            total_count = db.execute("SELECT COUNT(*) FROM basketball_leagues").fetchone()[0]
            if total_count == 0:
                sync_leagues_to_db(11)
                sync_leagues_to_db(100)


def resolve_league(query: str | int, verband_id: int | None = None) -> sqlite3.Row | None:
    """Resolve league by liga_id, liganr, short_name, or fuzzy name match across federations."""
    _ensure_leagues_synced(verband_id)

    q_str = str(query).strip()
    if not q_str:
        return None

    v_clause = " AND verband_id = ?" if verband_id is not None else ""
    v_params = [verband_id] if verband_id is not None else []

    with connection() as db:
        # 1. Exact liga_id match if numeric
        if q_str.isdigit():
            row = db.execute(
                f"SELECT * FROM basketball_leagues WHERE liga_id = ?{v_clause}",
                [int(q_str), *v_params],
            ).fetchone()
            if row:
                return row

        # 2. Exact liganr match
        row = db.execute(
            f"SELECT * FROM basketball_leagues WHERE lower(liganr) = lower(?){v_clause}",
            [q_str, *v_params],
        ).fetchone()
        if row:
            return row

        # 3. Exact short_name match
        row = db.execute(
            f"SELECT * FROM basketball_leagues WHERE lower(short_name) = lower(?){v_clause}",
            [q_str, *v_params],
        ).fetchone()
        if row:
            return row

        # 4. Short name or liganame prefix/LIKE match
        row = db.execute(
            f"""
            SELECT * FROM basketball_leagues
            WHERE (lower(short_name) LIKE lower(?) OR lower(liganame) LIKE lower(?)){v_clause}
            ORDER BY length(liganame) ASC LIMIT 1
            """,
            [f"%{q_str}%", f"%{q_str}%", *v_params],
        ).fetchone()
        return row


def is_schedule_stale(liga_id: int) -> bool:
    """Check if schedule is missing or synced more than CACHE_TTL_HOURS ago."""
    with connection() as db:
        game_count = db.execute(
            "SELECT COUNT(*) FROM basketball_games WHERE liga_id = ?", (liga_id,)
        ).fetchone()[0]
        if game_count == 0:
            return True

        row = db.execute(
            "SELECT last_synced_at FROM basketball_league_sync WHERE liga_id = ?", (liga_id,)
        ).fetchone()

    if not row:
        return True

    synced_at = datetime.fromisoformat(row["last_synced_at"])
    cutoff = datetime.now(UTC) - timedelta(hours=CACHE_TTL_HOURS)
    return synced_at < cutoff


def list_leagues(
    verband_id: int | None = None,
    search: str | None = None,
    altersklasse: str | None = None,
    geschlecht: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """List or search leagues in database across federations."""
    _ensure_leagues_synced(verband_id)

    query = "SELECT * FROM basketball_leagues WHERE 1=1"
    params: list[object] = []

    if verband_id is not None:
        query += " AND verband_id = ?"
        params.append(verband_id)

    if search:
        query += " AND (lower(short_name) LIKE lower(?) OR lower(liganame) LIKE lower(?) OR lower(liganr) LIKE lower(?))"
        pattern = f"%{search.strip()}%"
        params.extend([pattern, pattern, pattern])

    if altersklasse:
        query += " AND lower(altersklasse) LIKE lower(?)"
        params.append(f"%{altersklasse.strip()}%")

    if geschlecht:
        query += " AND lower(geschlecht) LIKE lower(?)"
        params.append(f"%{geschlecht.strip()}%")

    query += " ORDER BY verband_id ASC, liganame ASC LIMIT ?"
    params.append(limit)

    with connection() as db:
        rows = db.execute(query, params).fetchall()

    return [
        {
            "liga_id": r["liga_id"],
            "verband_id": r["verband_id"],
            "liganr": r["liganr"],
            "short_name": r["short_name"],
            "liganname": r["liganame"],
            "klasse": r["klasse"],
            "altersklasse": r["altersklasse"],
            "geschlecht": r["geschlecht"],
            "bezirk": r["bezirk"],
            "kreis": r["kreis"],
        }
        for r in rows
    ]


def query_games(
    league_query: str | int,
    team_name: str | None = None,
    date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    referee_name: str | None = None,
    force_refresh: bool = False,
    limit: int = 25,
) -> dict:
    """Main unified game query engine with filtering."""
    league = resolve_league(league_query)
    if not league:
        return {
            "status": "league_not_found",
            "message": f"Could not find any league matching '{league_query}'. Use basketball_list_leagues to see available leagues.",
        }

    liga_id = league["liga_id"]

    # Ensure schedule is cached and fresh
    if force_refresh or is_schedule_stale(liga_id):
        sync_schedule_to_db(liga_id)

    sql = "SELECT * FROM basketball_games WHERE liga_id = ?"
    params: list[object] = [liga_id]

    if team_name:
        sql += " AND (lower(heim) LIKE lower(?) OR lower(gast) LIKE lower(?))"
        t_pattern = f"%{team_name.strip()}%"
        params.extend([t_pattern, t_pattern])

    if date:
        sql += " AND date(datum) = date(?)"
        params.append(date.strip())

    if date_from:
        sql += " AND date(datum) >= date(?)"
        params.append(date_from.strip())

    if date_to:
        sql += " AND date(datum) <= date(?)"
        params.append(date_to.strip())

    if referee_name:
        sql += " AND lower(schiedsrichter) LIKE lower(?)"
        params.append(f"%{referee_name.strip()}%")

    sql += " ORDER BY datum ASC LIMIT ?"
    params.append(limit)

    with connection() as db:
        rows = db.execute(sql, params).fetchall()

    games = [
        {
            "spiel_nr": r["spiel_nr"],
            "spieltag": r["spieltag"],
            "datum": r["datum"],
            "heim": r["heim"],
            "gast": r["gast"],
            "spielhalle": r["spielhalle"],
            "schiedsrichter": r["schiedsrichter"],
        }
        for r in rows
    ]

    return {
        "status": "success",
        "league": {
            "liga_id": league["liga_id"],
            "liganr": league["liganr"],
            "short_name": league["short_name"],
            "liganame": league["liganame"],
        },
        "count": len(games),
        "games": games,
    }
